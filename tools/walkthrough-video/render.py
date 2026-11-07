"""Render the Langfuse walkthrough from real screenshots.
python render.py plan.json out.mp4 [audio.mp3]
plan.json: {"fps":30,"scenes":[{"id","dur","label","shots":[{"img","at"}],"zoom":[x,y,w,h]|null,"hl":[[x,y,w,h],...],"card":{...}|null}]}"""
import json, subprocess, sys, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
INK = (15, 30, 51); GREEN = (46, 211, 154); WHITE = (255, 255, 255); SLATE = (175, 192, 211); RED = (217, 72, 59)
SHOTS = "/opt/capture/shots"

def font(size, bold=False):
    for p in (["/usr/share/fonts/truetype/inter/Inter-Bold.ttf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf"] if bold else
              ["/usr/share/fonts/truetype/inter/Inter-Regular.ttf", "/usr/share/fonts/opentype/inter/Inter-Regular.otf"]) + \
             [f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()

F_LABEL = font(30, True); F_T1 = font(76, True); F_T2 = font(36); F_LIST = font(40)
cache = {}
def shot(name):
    if name not in cache:
        cache[name] = Image.open(f"{SHOTS}/{name}.png").convert("RGB")
    return cache[name]

def ease(t):
    t = max(0.0, min(1.0, t)); return t * t * (3 - 2 * t)

def fit169(box, minw=880):
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    w = max(w * 1.35, minw, h * 1.35 * 16 / 9); h = w * 9 / 16
    x = min(max(0, cx - w / 2), W - w); y = min(max(0, cy - h / 2), H - h)
    return [x, y, w, h]

def logo(d, x, y, s=1.0):
    d.rounded_rectangle([x, y, x + 64 * s, y + 64 * s], radius=14 * s, fill=(34, 55, 79))
    pts = [(x + 14 * s, y + 34 * s), (x + 26 * s, y + 46 * s), (x + 50 * s, y + 18 * s)]
    d.line(pts, fill=WHITE, width=int(5 * s), joint="curve")
    for (px, py), c, r in zip(pts, [GREEN, WHITE, GREEN], [6, 6, 7]):
        d.ellipse([px - r * s, py - r * s, px + r * s, py + r * s], fill=c)

def card(c, t):
    im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
    a = ease(t / 0.6)
    logo(d, 160, 220, 1.4)
    d.text((160, 360), c["title"], font=F_T1, fill=WHITE)
    yy = 470
    for line in c.get("sub", []):
        d.text((160, yy), line, font=F_T2, fill=SLATE); yy += 52
    yy += 30
    for i, item in enumerate(c.get("list", [])):
        if t > 0.8 + i * 0.9:
            d.ellipse([160, yy + 14, 176, yy + 30], fill=GREEN)
            d.text((200, yy), item, font=F_LIST, fill=WHITE)
        yy += 64
    if a < 1:
        im = Image.blend(Image.new("RGB", (W, H), INK), im, a)
    return im

def frame(sc, t):
    if sc.get("card"):
        return card(sc["card"], t)
    shots = sc["shots"]
    cur = [s for s in shots if t >= s["at"]][-1]
    base = shot(cur["img"])
    seg_t = t - cur["at"]
    seg_end = next((s["at"] for s in shots if s["at"] > cur["at"]), sc["dur"])
    zoom = cur.get("zoom")
    if zoom:
        z = fit169(zoom)
        k = ease((seg_t - 0.6) / 2.2)
        rect = [0 + (z[0]) * k, 0 + (z[1]) * k, W + (z[2] - W) * k, H + (z[3] - H) * k]
    else:
        rect = [0, 0, W, H]
    sx, sy = rect[2] / W, rect[3] / H
    kk = base.width / W
    img = base.crop((int(rect[0] * kk), int(rect[1] * kk), int((rect[0] + rect[2]) * kk), int((rect[1] + rect[3]) * kk))).resize((W, H), Image.BILINEAR, reducing_gap=2.0)
    hls = cur.get("hl", [])
    shown = []
    for i, hb in enumerate(hls):
        start = cur.get("hl_start", 1.6) + i * cur.get("hl_gap", (seg_end - cur["at"] - 2.4) / max(1, len(hls)))
        if seg_t >= start:
            shown.append((hb, ease((seg_t - start) / 0.35)))
    if shown:
        dim = Image.new("RGBA", (W, H), (15, 30, 51, 0))
        dd = ImageDraw.Draw(dim)
        alpha = max(a for _, a in shown)
        dd.rectangle([0, 0, W, H], fill=(15, 30, 51, int(95 * alpha)))
        boxes = []
        for (x, y, w, h), a in shown:
            pad = 10
            bx = [(x - pad - rect[0]) / sx, (y - pad - rect[1]) / sy, (x + w + pad - rect[0]) / sx, (y + h + pad - rect[1]) / sy]
            boxes.append((bx, a))
            dd.rounded_rectangle(bx, radius=10, fill=(0, 0, 0, 0))
        img = Image.alpha_composite(img.convert("RGBA"), dim).convert("RGB")
        d = ImageDraw.Draw(img)
        for bx, a in boxes:
            col = RED if cur.get("hl_red") else GREEN
            d.rounded_rectangle(bx, radius=10, outline=col, width=max(1, int(5 * a)))
    d = ImageDraw.Draw(img)
    lab = sc.get("label")
    if lab:
        tw = d.textlength(lab, font=F_LABEL)
        d.rounded_rectangle([40, H - 104, 40 + tw + 48, H - 40], radius=14, fill=INK)
        d.text((64, H - 92), lab, font=F_LABEL, fill=WHITE)
    return img

def main():
    plan = json.load(open(sys.argv[1])); out = sys.argv[2]; audio = sys.argv[3] if len(sys.argv) > 3 else None
    fps = plan.get("fps", 30); scenes = plan["scenes"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-i", audio, "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    XF = 0.4; prev_last = None; n = 0
    for si, sc in enumerate(scenes):
        nf = int(round(sc["dur"] * fps))
        for f in range(nf):
            t = f / fps
            im = frame(sc, t)
            if prev_last is not None and t < XF:
                im = Image.blend(prev_last, im, t / XF)
            p.stdin.write(im.tobytes()); n += 1
        prev_last = frame(sc, (nf - 1) / fps)
        print(f"scene {si+1}/{len(scenes)} {sc['id']} {sc['dur']:.1f}s", flush=True)
    p.stdin.close(); p.wait()
    print("frames", n, "->", out)

if __name__ == "__main__":
    main()
