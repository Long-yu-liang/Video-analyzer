"""FFmpeg probing, frame extraction and visual request copies."""
import base64
import json
import math
from pathlib import Path
import re
import subprocess


def run(args):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=90).stdout
    except FileNotFoundError:
        raise ValueError('请安装 FFmpeg，并将 ffmpeg / ffprobe 加入 PATH。')
    except subprocess.CalledProcessError:
        raise ValueError('视频无法解码；请使用可播放的 MP4 文件。')


def probe(path, runner=None):
    runner = runner or run
    info = json.loads(runner(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    video = next((x for x in info['streams'] if x['codec_type'] == 'video'), None)
    if not video:
        raise ValueError('文件不包含视频画面。')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError('本 Demo 仅接受 0–60 秒视频；请先裁剪。')
    return {'duration_s': duration, 'width': video['width'], 'height': video['height'],
            'has_audio': any(x['codec_type'] == 'audio' for x in info['streams'])}


def extract(path, directory, duration, fps):
    # fps filter resets its output timebase; use actual source PTS via showinfo after selection.
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{1/fps:.9f})',scale=768:768:force_original_aspect_ratio=decrease,showinfo"
    command = ['ffmpeg', '-hide_banner', '-i', str(path), '-an', '-vf', vf,
               '-fps_mode', 'vfr', '-frames:v', '120', '-q:v', '4', str(directory / '%03d.jpg')]
    try:
        proc = subprocess.run(command, check=True, capture_output=True, timeout=90)
    except subprocess.CalledProcessError:
        raise ValueError('抽帧失败。')
    pts = [float(x) for x in re.findall(r'pts_time:([\d.eE+-]+)', proc.stderr.decode(errors='replace'))]
    images = sorted(directory.glob('*.jpg'))
    if not images or len(pts) < len(images):
        raise ValueError('无法提取带时间戳的画面。')
    origin = pts[0]
    return [{'time_s': round(t-origin, 6), 'data': base64.b64encode(p.read_bytes()).decode()}
            for p, t in zip(images, pts)]


def strip_audio(path, output):
    """Remux a visual-only request copy without changing the source file."""
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                        '-c:v', 'copy', '-an', '-sn', '-dn', '-movflags', '+faststart',
                        str(output)], check=True, capture_output=True, timeout=90)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('无法移除视频音轨；未调用模型。请检查 FFmpeg 或视频文件。')
    return output


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


def clip_video(path, output, start_s, end_s, runner=None, max_video=12 * 1024 * 1024):
    runner = runner or run
    runner(['ffmpeg', '-v', 'error', '-i', str(path), '-ss', f'{start_s:.6f}',
         '-t', f'{end_s-start_s:.6f}', '-map', '0:v:0', '-an', '-sn', '-dn',
         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
         '-movflags', '+faststart', str(output)])
    if output.stat().st_size > max_video:
        raise ValueError('分段编码后仍超过 12 MiB，请压缩源视频。')
    return output
