"""FFmpeg probing, frame extraction and visual request copies."""
import base64
import json
import math
from pathlib import Path
import re
import subprocess
from .. import settings


class MediaProcessingError(ValueError):
    """A local media failure, not a model error or proof that the source is corrupt."""
    def __init__(self, operation, *, tool='ffmpeg', exit_code=None, stderr='', code='process_failed', message=None):
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        stderr = (stderr or '')[-6000:]
        label = {'probe': '视频信息读取', 'transcode': '视频分段/重新编码',
                 'remux': '视频去音轨/封装', 'extract': '视频抽帧', 'verify': '预处理产物校验'}[operation]
        if message is None:
            status = f'退出码 {exit_code}' if exit_code is not None else code
            message = f'{label}失败（{tool}，{status}）；这不等于原视频无法播放。'
            message += (' 错误详情：'+stderr[-500:].strip()) if stderr.strip() else ' 进程未提供错误文本，请检查本机 FFmpeg 环境。'
        self.detail = {'stage': 'media_preprocessing', 'operation': operation, 'tool': tool,
                       'code': code, 'exit_code': exit_code, 'stderr': stderr, 'message': message}
        super().__init__(message)


def _operation(args):
    if Path(str(args[0])).stem.lower() == 'ffprobe':
        return 'probe'
    if '-c:v' in args and args[args.index('-c:v')+1] == 'copy':
        return 'remux'
    return 'transcode'


def run(args):
    operation = _operation(args)
    tool = Path(str(args[0])).name
    args = [settings.tool(tool), *args[1:]]
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=90).stdout
    except FileNotFoundError as error:
        raise MediaProcessingError(operation, tool=tool, code='missing_tool',
            message='请安装 FFmpeg，并将 ffmpeg / ffprobe 加入 PATH。') from error
    except subprocess.CalledProcessError as error:
        raise MediaProcessingError(operation, tool=tool, exit_code=error.returncode, stderr=error.stderr) from error
    except subprocess.TimeoutExpired as error:
        raise MediaProcessingError(operation, tool=tool, code='timeout', stderr=error.stderr,
            message=f'{tool} {operation} 处理超过 90 秒，尚未调用本次模型请求。') from error


def probe(path, runner=None):
    runner = runner or run
    info = json.loads(runner(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    video = next((x for x in info['streams'] if x['codec_type'] == 'video'), None)
    if not video:
        raise ValueError('文件不包含视频画面。')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError('本 Demo 仅接受 0–60 秒视频；请先裁剪。')
    result = {'duration_s': duration, 'width': video['width'], 'height': video['height'],
              'has_audio': any(x['codec_type'] == 'audio' for x in info['streams']),
              'video_codec': video.get('codec_name', 'unknown')}
    for key, source in (('video_duration_s', 'duration'), ('video_start_s', 'start_time')):
        try:
            value = float(video.get(source, ''))
            if math.isfinite(value):
                result[key] = value
        except (TypeError, ValueError):
            pass
    try:
        count = int(video.get('nb_frames', ''))
        if count > 0:
            result['video_frame_count'] = count
    except (TypeError, ValueError):
        pass
    return result


def extract(path, directory, duration, fps):
    # fps filter resets its output timebase; use actual source PTS via showinfo after selection.
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{1/fps:.9f})',scale=768:768:force_original_aspect_ratio=decrease,showinfo"
    command = [settings.tool('ffmpeg'), '-hide_banner', '-i', str(path), '-an', '-vf', vf,
               '-fps_mode', 'vfr', '-frames:v', '120', '-q:v', '4', str(directory / '%03d.jpg')]
    try:
        proc = subprocess.run(command, check=True, capture_output=True, timeout=90)
    except FileNotFoundError as error:
        raise MediaProcessingError('extract', code='missing_tool', message='请安装 FFmpeg，并将 ffmpeg 加入 PATH。') from error
    except subprocess.CalledProcessError as error:
        raise MediaProcessingError('extract', exit_code=error.returncode, stderr=error.stderr) from error
    except subprocess.TimeoutExpired as error:
        raise MediaProcessingError('extract', code='timeout', stderr=error.stderr,
            message='视频抽帧超过 90 秒，尚未调用本次模型请求。') from error
    pts = [float(x) for x in re.findall(r'pts_time:([\d.eE+-]+)', proc.stderr.decode(errors='replace'))]
    images = sorted(directory.glob('*.jpg'))
    if not images or len(pts) < len(images):
        raise ValueError('无法提取带时间戳的画面。')
    origin = pts[0]
    return [{'time_s': round(t-origin, 6), 'data': base64.b64encode(p.read_bytes()).decode()}
            for p, t in zip(images, pts)]


def strip_audio(path, output):
    """Remux a visual-only request copy without changing the source file."""
    # Never add -ss/-t here: output-side stream-copy seeking can drop the opening GOP.
    run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
         '-c:v', 'copy', '-an', '-sn', '-dn', '-movflags', '+faststart', str(output)])
    return output


def prepare_full_video(path, output, metadata, mode, *, remux=None, prober=None, max_video=12 * 1024 * 1024):
    """Use the entire original picture stream; no cropping or codec/preset fallback."""
    if mode not in ('direct', 'frames'):
        raise ValueError('未知视频输入方式。')
    if mode == 'frames':
        return path, dict(metadata), 'original_frames'
    if metadata.get('video_codec') != 'h264':
        raise ValueError('原码流直传仅用于已确认的 H.264 视频。')
    if not metadata['has_audio']:
        return path, dict(metadata), 'original_video'
    request_path = (remux or strip_audio)(path, output)
    if not request_path.is_file() or not 0 < request_path.stat().st_size <= max_video:
        raise MediaProcessingError('verify', code='invalid_output', message='去音轨产物为空或超过 12 MiB，未调用模型。')
    actual = (prober or probe)(request_path)
    valid = not actual['has_audio'] and actual.get('video_codec') == metadata['video_codec']
    valid = valid and all(actual[k] == metadata[k] for k in ('width', 'height'))
    for field in ('video_start_s', 'video_duration_s'):
        if field in metadata:
            valid = valid and field in actual and abs(actual[field] - metadata[field]) <= 0.001
    if 'video_frame_count' in metadata:
        valid = valid and actual.get('video_frame_count') == metadata['video_frame_count']
    if not valid:
        raise MediaProcessingError('verify', code='timeline_changed',
            message='去音轨产物的画面流、帧数或时间信息不一致，未调用模型；不会使用可能丢失片头的产物。')
    return request_path, actual, 'stream_copy_without_audio'


def list_samples(root):
    folder = root / 'mp4-example'
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob('*.mp4') if p.is_file())


def read_sample(root, name, max_video):
    if not name or Path(name).name != name or not name.lower().endswith('.mp4'):
        raise ValueError('只能选择 mp4-example 中的 MP4 文件。')
    folder = (root / 'mp4-example').resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise ValueError('样例视频不存在。')
    data = path.read_bytes()
    if not data or len(data) > max_video:
        raise ValueError('视频须为非空 MP4，大小不超过 12 MiB。')
    return data


def segment_windows(duration):
    """Twenty-second ownership windows, each later request sees one preceding second."""
    if duration <= 25.:
        return [(0., duration, 0., duration)]
    windows = []
    core_start = 0.
    while core_start < duration:
        core_end = min(duration, core_start + 20.)
        windows.append((core_start, core_end, max(0., core_start - 1.), core_end))
        core_start = core_end
    return windows


def _usable_segment(output, duration_s, max_video, prober):
    """True when a nonzero FFmpeg exit still left a complete H.264 segment."""
    if not output.is_file() or output.stat().st_size <= 0:
        return False
    try:
        info = prober(output)
    except (MediaProcessingError, ValueError, OSError, KeyError):
        return False
    duration = info.get('duration_s')
    usable = info.get('video_codec') == 'h264' and not info.get('has_audio')
    usable = usable and isinstance(duration, (int, float)) and abs(duration - duration_s) <= 0.15
    if usable and output.stat().st_size > max_video:
        raise ValueError('分段编码后仍超过 12 MiB，请压缩源视频。')
    return usable


def clip_video(path, output, start_s, end_s, runner=None, prober=None, max_video=12 * 1024 * 1024):
    """Cut one segment. FFmpeg 9 on Windows can exit 8987 after a finished file, or mid-write."""
    runner = runner or run
    prober = prober or probe
    duration_s = end_s - start_s
    shared = ['ffmpeg', '-y', '-nostdin', '-v', 'error', '-i', str(path), '-ss', f'{start_s:.6f}',
              '-t', f'{duration_s:.6f}', '-map', '0:v:0', '-an', '-sn', '-dn']
    ending = ['-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)]
    # libx264 is smaller, but this build often aborts in it. Media Foundation is retried
    # because the same exit code sometimes belongs to a truncated file and sometimes does not.
    commands = [
        [*shared, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', *ending],
        *[[*shared, '-c:v', 'h264_mf', '-rate_control', 'cbr', '-b:v', '2M', *ending]] * 3,
    ]
    failure = None
    for command in commands:
        if output.exists():
            output.unlink()
        try:
            runner(command)
        except MediaProcessingError as error:
            if error.detail['code'] in ('missing_tool', 'timeout'):
                raise
            if _usable_segment(output, duration_s, max_video, prober):
                return output
            failure = failure or error
            continue
        if output.is_file() and 0 < output.stat().st_size <= max_video:
            return output
        if output.is_file() and output.stat().st_size > max_video:
            raise ValueError('分段编码后仍超过 12 MiB，请压缩源视频。')
    if failure:
        raise failure
    raise MediaProcessingError('transcode', code='invalid_output', message='分段编码未生成有效视频，未调用模型。')
