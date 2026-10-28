"""Build an offline, single-file reading edition from the project summary."""
import base64
import hashlib
import html
import json
import mimetypes
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '视频分析器_项目总结.md'
OUTPUT = SOURCE.with_suffix('.html')

CSS = r'''
:root {
  --bg:#f8f7f2; --surface:#ffffff; --fg:#26332d;
  --muted:#626b65; --border:#d8ddd6; --accent:#315e49;
  --accent-soft:color-mix(in oklch,var(--accent) 8%,var(--surface));
  --fg-soft:color-mix(in oklch,var(--fg) 4%,var(--surface));
  --font-display:"Songti SC","STSong","SimSun",serif;
  --font-body:"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;
  --fs-title:clamp(44px,5.6vw,76px); --fs-section:clamp(30px,3.2vw,42px);
  --fs-topic:22px; --fs-body:17px; --fs-meta:13px; --fs-small:14px;
  --gap-xs:8px; --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  --gap-xl:56px; --gap-2xl:96px;
  --radius-panel:12px; --radius-row:8px; --radius-pill:999px;
}
*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%;scroll-padding-top:28px}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font-body);font-size:var(--fs-body);line-height:1.6;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
img,svg{display:block;max-width:100%}img{height:auto}button{font:inherit;cursor:pointer}a{color:inherit;text-decoration:none}
button,a,summary{-webkit-tap-highlight-color:transparent}a:hover{text-decoration:underline;text-underline-offset:5px}
:focus-visible{outline:3px solid var(--accent);outline-offset:5px}
p{text-wrap:pretty;overflow-wrap:anywhere}h1,h2,h3,h4{font-family:var(--font-display);text-wrap:balance;overflow-wrap:anywhere}
strong{font-weight:600}code{font-family:var(--font-body);font-size:.88em;background:var(--fg-soft);padding:2px 5px;border-radius:4px;overflow-wrap:anywhere;word-break:break-word}
.num{font-variant-numeric:tabular-nums;letter-spacing:.02em}
.skip{position:absolute;top:12px;left:12px;padding:12px 18px;z-index:20;background:var(--surface);transform:translateY(-180%)}.skip:focus{transform:none}
.shell{max-width:1360px;margin:0 auto;padding:0 36px}
.masthead{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:26px 0 22px;border-bottom:1px solid var(--border);font-size:var(--fs-meta);color:var(--muted);letter-spacing:.02em}
.brand{display:flex;align-items:center;gap:10px;color:var(--fg);font-weight:600}.brand svg{width:25px;height:25px}
.layout{display:grid;grid-template-columns:184px minmax(0,1fr);gap:56px;align-items:start}
.rail{position:sticky;top:30px;padding-top:60px;min-width:0}
.toc-panel{border-top:1px solid var(--fg);padding-top:15px}.toc-panel summary{min-height:44px;cursor:pointer;font-size:var(--fs-small);font-weight:600;list-style:none;display:flex;justify-content:space-between;align-items:center}.toc-panel summary::-webkit-details-marker{display:none}.toc-panel summary::after{content:'−';font-size:22px}.toc-panel:not([open]) summary::after{content:'+'}
.toc{display:grid;gap:4px;margin:10px 0}.toc a{display:grid;grid-template-columns:25px minmax(0,1fr);gap:8px;padding:10px 0;min-height:44px;font-size:var(--fs-small);color:var(--muted)}.toc a[aria-current="location"]{color:var(--fg);font-weight:600}.toc a:hover{color:var(--fg)}.toc .num{font-size:var(--fs-meta)}
.rail-note{font-size:var(--fs-meta);color:var(--muted);line-height:1.6;margin-top:28px}
.article{min-width:0;padding-bottom:72px}
.hero{padding:64px 0 44px;border-bottom:1px solid var(--fg)}
.eyebrow{font-size:var(--fs-meta);color:var(--accent);margin:0 0 22px;letter-spacing:.02em}
h1{font-size:var(--fs-title);line-height:1.16;letter-spacing:-.025em;font-weight:600;margin:0 0 24px}
.lead{max-width:56ch;color:var(--muted);font-size:var(--fs-body);line-height:1.6;margin:0 0 30px}
.hero-meta{display:flex;gap:12px 30px;flex-wrap:wrap;font-size:var(--fs-meta);color:var(--muted)}
.chapter{padding:56px 0 44px;border-bottom:1px solid var(--border);min-width:0}
.chapter:last-child{border-bottom:0}
.chapter-heading{display:flex;gap:20px;align-items:baseline;margin-bottom:30px}.chapter-no{font-size:var(--fs-meta);color:var(--accent);flex:none}
h2{font-size:var(--fs-section);font-weight:600;line-height:1.22;letter-spacing:-.02em;margin:0}
h3{font-size:var(--fs-topic);font-weight:600;line-height:1.4;letter-spacing:-.005em;margin:0 0 20px}
h4{font-size:var(--fs-body);font-weight:600;line-height:1.6;margin:0 0 12px}
.body-copy p,.topic>p{max-width:65ch;margin:0 0 20px}
.body-copy>p:last-child{margin-bottom:0}.topic{margin:40px 0 0;min-width:0}.topic:first-child{margin-top:0}
.chapter-goal .topic{margin-top:48px;padding-top:28px;border-top:1px solid var(--border)}
.figure{margin:32px 0 36px;min-width:0;position:relative}
.figure-image{width:100%;border:1px solid var(--border);border-radius:var(--radius-row);background:var(--surface)}
.figure figcaption{display:flex;gap:12px;margin-top:12px;color:var(--muted);font-size:var(--fs-meta);line-height:1.6;letter-spacing:.01em;overflow-wrap:anywhere}
.figure-action{position:absolute;right:12px;top:12px;min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center;gap:6px;padding:8px 12px;background:var(--surface);color:var(--fg);border:1px solid var(--muted);border-radius:var(--radius-row);font-size:var(--fs-meta);letter-spacing:.02em;box-shadow:0 2px 12px color-mix(in oklch,var(--fg) 8%,transparent)}
.figure-action[hidden]{display:none}.figure-action:hover{background:var(--bg)}.figure-action svg{width:18px;height:18px;flex:none}
.table-wrap{margin:28px 0 32px;min-width:0}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:var(--fs-small);line-height:1.6;text-align:left;background:var(--surface)}
th,td{padding:17px 18px;vertical-align:top;border-bottom:1px solid var(--border);overflow-wrap:anywhere;word-break:normal}
th{font-weight:600;background:var(--fg-soft);border-top:1px solid var(--fg);color:var(--fg)}td:first-child{font-weight:550}tbody tr:last-child td{border-bottom:1px solid var(--fg)}
td p{margin:0}table code{background:none;padding:0;font-size:inherit}
.compare{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;margin:22px 0 26px}
.compare-pane{padding:22px 24px;background:var(--surface);border:1px solid var(--border);min-width:0}.compare-pane:first-child{background:var(--fg-soft)}
.compare-label{display:block;font-size:var(--fs-meta);letter-spacing:.02em;margin-bottom:14px;font-weight:600;color:var(--muted)}.compare-pane p{margin:0;font-size:var(--fs-body);line-height:1.6;overflow-wrap:anywhere}
.compare-pane.after .compare-label{color:var(--fg)}
.chapter-features .topic{padding-top:30px;border-top:1px solid var(--border)}
.case{padding:30px 0 8px;border-top:1px solid var(--border)}.case:first-child{padding-top:0;border-top:0}
.case-pair{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:30px}.case-part{min-width:0}.case-part p{margin:0 0 20px;font-size:var(--fs-body)}.case-part:first-child p{color:var(--muted)}.case-label{font-size:var(--fs-small);margin:0 0 12px;font-weight:600}
.chapter-lessons .topic>p{padding:0 0 18px;margin-bottom:20px;border-bottom:1px solid var(--border)}
.report-footer{border-top:1px solid var(--fg);padding-top:24px;margin-top:24px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;font-size:var(--fs-meta);color:var(--muted)}
.report-footer a{min-height:44px;display:flex;align-items:center;gap:6px}
.image-dialog{max-width:none;width:min(96vw,1760px);max-height:94dvh;padding:0;border:1px solid var(--muted);border-radius:var(--radius-panel);background:var(--surface);color:var(--fg)}
.image-dialog::backdrop{background:color-mix(in oklch,var(--fg) 78%,transparent)}.dialog-head{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:12px 18px;border-bottom:1px solid var(--border)}.dialog-head h2{font-size:var(--fs-topic);letter-spacing:-.005em;line-height:1.4}
.close-image{min-width:44px;min-height:44px;background:var(--surface);color:var(--fg);border:1px solid var(--muted);border-radius:var(--radius-row);padding:8px 14px;letter-spacing:.02em}.close-image:hover{background:var(--bg)}
.dialog-body{padding:16px;overflow:auto;max-height:calc(94dvh - 76px)}.dialog-body img{width:100%;height:auto}.image-status{font-size:var(--fs-small);margin:0}
@media(max-width:1100px){.shell{padding:0 28px}.layout{gap:32px;grid-template-columns:156px minmax(0,1fr)}th,td{padding:15px 12px}}
@media(max-width:920px){
  .shell{padding:0 22px}.layout,.compare,.case-pair{grid-template-columns:minmax(0,1fr)}.layout{gap:0}.rail{position:static;padding-top:24px}.rail-note{display:none}.toc{grid-template-columns:minmax(0,1fr)}.toc-panel{border-top:0;border-bottom:1px solid var(--border);padding-top:0;padding-bottom:8px}.toc a{padding:8px 0}.hero{padding:38px 0 32px}.chapter{padding:40px 0 30px}.chapter-heading{gap:14px;margin-bottom:26px}.chapter-no{min-width:20px}h2{font-size:30px;letter-spacing:-.015em}.topic{margin-top:32px}.figure{margin:25px 0}.compare{gap:12px}.compare-pane{padding:18px}.case-pair{gap:8px}
  table,thead,tbody,tr,th,td{display:block;width:100%}thead{display:none}table{background:transparent}tbody tr{padding:18px;margin-bottom:16px;background:var(--surface);border:1px solid var(--border)}td{border:0;padding:0 0 18px}td:last-child,tbody tr:last-child td{border:0;padding-bottom:0}td::before{content:attr(data-label);display:block;font-size:var(--fs-meta);font-weight:600;color:var(--muted);margin-bottom:7px}td:first-child{font-weight:600}.masthead{padding:20px 0;align-items:flex-start}.masthead>span{font-size:var(--fs-meta);text-align:right}
}
@media(max-width:480px){.shell{padding:0 18px}.masthead{gap:12px}.brand{gap:6px}.masthead>span{max-width:7em}h1{font-size:44px}.figure-action{right:8px;top:8px;padding:8px}.figure-action span{display:none}.dialog-head{padding:12px}.dialog-body{padding:8px}.chapter-heading{gap:10px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation:none!important;transition:none!important}.skip{transform:none;position:static;display:inline-block}}
@media print{
  @page{size:A4;margin:16mm}body{background:var(--surface);font-size:11pt}.shell{max-width:none;padding:0}.masthead{padding-top:0}.layout{display:block}.rail,.skip,.figure-action,.image-dialog,.report-footer a{display:none!important}.hero{padding:28px 0}h1{font-size:36pt}.chapter{padding:28px 0;break-before:auto}.chapter-heading{margin-bottom:20px}h2{font-size:24pt}h3{font-size:16pt}h2,h3,h4{break-after:avoid}.figure{break-inside:avoid;margin:20px 0}p{orphans:3;widows:3}.article{padding-bottom:0}.compare,.case-pair{display:block}.compare-pane{margin-bottom:12px;padding:14px}table{display:table;font-size:9pt}thead{display:table-header-group}tbody{display:table-row-group}tr{display:table-row;break-inside:avoid}th,td{display:table-cell;width:auto;padding:9px 10px}td::before{display:none}.report-footer{font-size:9pt}.figure-image{max-height:220mm;object-fit:contain}
}
'''

JS = r'''
(() => {
  const links = [...document.querySelectorAll('.toc a')];
  const chapters = [...document.querySelectorAll('.chapter')];
  const toc = document.querySelector('.toc-panel');
  if (matchMedia('(max-width:920px)').matches) toc.open = false;
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a,b) => a.boundingClientRect.top-b.boundingClientRect.top);
      if (!visible.length) return;
      const id = visible[0].target.id;
      links.forEach(link => link.getAttribute('href') === '#' + id ? link.setAttribute('aria-current','location') : link.removeAttribute('aria-current'));
    }, {rootMargin:'-5% 0px -65% 0px',threshold:0});
    chapters.forEach(chapter => observer.observe(chapter));
  }
  links.forEach(link => link.addEventListener('click', () => {
    if (matchMedia('(max-width:920px)').matches) toc.open = false;
  }));
  const dialog = document.querySelector('#image-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const image = dialog.querySelector('img');
  const title = dialog.querySelector('h2');
  const status = dialog.querySelector('.image-status');
  let origin = null, pending = null;
  function clearWait() { clearTimeout(pending); pending = null; }
  image.onload = () => { clearWait(); image.hidden=false; status.hidden=true; };
  image.onerror = () => { clearWait(); image.hidden=true; status.hidden=false; status.textContent='图片未能打开，请关闭此窗口，在正文中查看。'; };
  document.querySelectorAll('.figure-action').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => {
      origin=button; clearWait();
      const source = button.closest('figure').querySelector('img');
      title.textContent=source.alt; image.alt=source.alt;
      status.textContent='正在打开图片…'; status.hidden=false; image.hidden=true;
      image.src=source.src; dialog.showModal();
      if (image.complete && image.naturalWidth) { image.hidden=false; status.hidden=true; }
      else pending=setTimeout(() => { status.textContent='图片打开较慢，可关闭此窗口后在正文中查看。'; },15000);
    });
  });
  dialog.querySelector('.close-image').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { clearWait(); image.removeAttribute('src'); if (origin) origin.focus(); });
})();
'''


def inline(text):
    tokens = re.split(r'(`[^`]+`)', text)
    result = []
    for token in tokens:
        if token.startswith('`') and token.endswith('`'):
            result.append('<code>' + html.escape(token[1:-1]) + '</code>')
        else:
            safe = html.escape(token)
            result.append(re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', safe))
    return ''.join(result)


def parse_blocks(lines):
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        image = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', line.strip())
        if image:
            blocks.append(('image', image.groups()))
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [cell.strip() for cell in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', cell.replace(' ', '')) for cell in cells):
                    rows.append(cells)
                i += 1
            assert rows and all(len(row) == len(rows[0]) for row in rows), 'Invalid source table'
            blocks.append(('table', rows))
            continue
        paragraph = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('|', '![', '#')):
            paragraph.append(lines[i])
            i += 1
        if not paragraph:
            raise ValueError('Unsupported source line: ' + lines[i])
        value = ''
        for index, part in enumerate(paragraph):
            value += inline(part.rstrip())
            if index + 1 < len(paragraph):
                next_numbered = re.match(r'^[（(]\d+[）)]', paragraph[index+1])
                value += '<br>' if part.endswith('  ') or next_numbered else ' '
        blocks.append(('p', (value, '\n'.join(paragraph))))
    return blocks


class Renderer:
    def __init__(self):
        self.images = []
        self.paragraphs = []
        self.tables = 0

    def render(self, blocks):
        result = []
        for kind, value in blocks:
            if kind == 'p':
                self.paragraphs.append(value[1])
                result.append('<p>' + value[0] + '</p>')
            elif kind == 'table':
                self.tables += 1
                headers = value[0]
                result.append('<div class="table-wrap"><table><thead><tr>' + ''.join('<th scope="col">'+inline(cell)+'</th>' for cell in headers) + '</tr></thead><tbody>')
                for row in value[1:]:
                    result.append('<tr>' + ''.join('<td data-label="'+html.escape(label,quote=True)+'">'+inline(cell)+'</td>' for label, cell in zip(headers,row)) + '</tr>')
                result.append('</tbody></table></div>')
            elif kind == 'image':
                alt, relative = value
                path = (ROOT / relative).resolve()
                if not path.is_file():
                    raise FileNotFoundError('Missing document image: ' + relative)
                data = path.read_bytes()
                mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                width, height = struct.unpack('>II', data[16:24]) if data.startswith(b'\x89PNG') else (0,0)
                number = len(self.images)+1
                self.images.append({'path':relative,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'width':width,'height':height})
                uri='data:'+mime+';base64,'+base64.b64encode(data).decode('ascii')
                dimensions=f' width="{width}" height="{height}"' if width else ''
                result.append('<figure class="figure"><img class="figure-image" src="'+uri+'" alt="'+html.escape(alt,quote=True)+'"'+dimensions+' loading="'+('eager' if number==1 else 'lazy')+'" decoding="async">'
                    '<button class="figure-action" type="button" hidden aria-label="放大图片：'+html.escape(alt,quote=True)+'"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M9 3H3v6M15 3h6v6M21 15v6h-6M9 21H3v-6"/></svg><span>放大查看</span></button>'
                    '<figcaption><span class="num">图 '+str(number).zfill(2)+'</span><span>'+html.escape(alt)+'</span></figcaption></figure>')
        return ''.join(result)


def short_title(text):
    return re.sub(r'^(?:[一二三四五六七八九十]+\s+|\d+[、.\s]+)', '', text).strip()


def main():
    source = SOURCE.read_text(encoding='utf-8')
    original_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    groups = re.split(r'^## (.+)\n', source, flags=re.M)
    renderer = Renderer()
    chapters, navigation = [], []
    for index in range(1, len(groups), 2):
        number = (index+1)//2
        title, content = groups[index:index+2]
        display_title = short_title(title)
        identity='chapter-'+str(number)
        kind = ('goal' if '项目目标' in title else 'features' if '功能优化' in title else
                'problems' if '问题难点' in title else 'lessons' if '收获' in title else 'standard')
        navigation.append('<a href="#'+identity+'"><span class="num">'+str(number).zfill(2)+'</span><span>'+html.escape(display_title)+'</span></a>')
        pieces = re.split(r'^### (.+)\n', content, flags=re.M)
        body = '<div class="body-copy">'+renderer.render(parse_blocks(pieces[0].splitlines()))+'</div>'
        for part in range(1, len(pieces), 2):
            subtitle, text = pieces[part:part+2]
            blocks = parse_blocks(text.splitlines())
            rendered = ''
            if kind == 'features':
                before = [block for block in blocks if block[0]=='p' and block[1][1].startswith('**原本：**')]
                after = [block for block in blocks if block[0]=='p' and block[1][1].startswith('**优化后：**')]
                if before and after:
                    before_html=renderer.render(before).replace('<strong>原本：</strong>','',1)
                    after_html=renderer.render(after).replace('<strong>优化后：</strong>','',1)
                    rendered='<div class="compare"><div class="compare-pane"><span class="compare-label">原本</span>'+before_html+'</div><div class="compare-pane after"><span class="compare-label">优化后</span>'+after_html+'</div></div>'
                    rendered+=renderer.render([block for block in blocks if block not in before+after])
            if kind == 'problems':
                split=next((i for i,block in enumerate(blocks) if block[0]=='p' and '（2）解决方案' in block[1][1]),None)
                if split is not None:
                    left=renderer.render(blocks[:split]).replace('<p><strong>（1）遇到的问题和难点</strong></p>','<h4 class="case-label">（1）遇到的问题和难点</h4>')
                    right=renderer.render(blocks[split:]).replace('<p><strong>（2）解决方案</strong></p>','<h4 class="case-label">（2）解决方案</h4>')
                    rendered='<div class="case-pair"><div class="case-part">'+left+'</div><div class="case-part">'+right+'</div></div>'
            if not rendered:
                rendered=renderer.render(blocks)
            body+='<div class="topic'+(' case' if kind=='problems' else '')+'"><h3>'+html.escape(subtitle)+'</h3>'+rendered+'</div>'
        chapters.append('<section class="chapter chapter-'+kind+'" id="'+identity+'" data-od-id="'+identity+'" aria-labelledby="'+identity+'-title"><div class="chapter-heading"><span class="chapter-no num" aria-hidden="true">'+str(number).zfill(2)+'</span><h2 id="'+identity+'-title">'+html.escape(display_title)+'</h2></div>'+body+'</section>')

    output='''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="视频分析器项目总结，记录模型选择、提示词优化、交互设计、功能改善与问题解决过程。">
<title>视频分析器项目总结 · 梁玉龙</title><style>'''+CSS+'''</style></head><body id="top">
<a class="skip" href="#report">跳到报告正文</a><div class="shell">
<div class="masthead"><div class="brand"><svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="2" y="4" width="24" height="17" rx="3"/><path d="m11 9 7 4-7 4V9ZM9 25h10"/></svg>视频分析器</div><span>项目实施与技术复盘 · 梁玉龙</span></div>
<div class="layout"><aside class="rail"><details class="toc-panel" open><summary>阅读目录</summary><nav class="toc" aria-label="报告章节">'''+''.join(navigation)+'''</nav></details><p class="rail-note">沿着一次视频创作的过程，回看选择、尝试与改进。</p></aside>
<main id="report" class="article" tabindex="-1"><header class="hero"><p class="eyebrow">项目总结</p><h1>视频分析器<br>项目总结</h1><p class="lead">从理解视频、发现交互机会，到生成可以试玩的小游戏。</p><div class="hero-meta"><span>作者 / 梁玉龙</span><span>内容 / 完整流程、模型与提示词、体验改进</span></div></header>
'''+''.join(chapters)+'''<footer class="report-footer"><span>视频分析器 · 项目总结</span><a href="#top">回到开头 ↑</a></footer></main></div></div>
<dialog id="image-dialog" class="image-dialog" aria-labelledby="image-dialog-title"><div class="dialog-head"><h2 id="image-dialog-title">图片预览</h2><button class="close-image" type="button">关闭图片</button></div><div class="dialog-body"><p class="image-status" role="status"></p><img alt="" hidden></div></dialog>
<script>'''+JS+'''</script></body></html>'''
    OUTPUT.write_text(output, encoding='utf-8')
    assert original_hash == hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'Source was changed'
    metadata = {'source':SOURCE.name,'source_sha256':original_hash,'output':OUTPUT.name,
                'chapters':len(chapters),'paragraphs':len(renderer.paragraphs),'tables':renderer.tables,
                'images':renderer.images,'self_contained':True,'bytes':OUTPUT.stat().st_size,
                'layout':'editorial long-form, native chapter navigation, before/after comparison',
                'source_unchanged':True}
    report = ROOT/'docs'/'qa'/'项目总结HTML_检查记录.json'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in metadata.items() if k!='images'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
