"""Render the geometric voice mark at native Windows icon resolutions."""
import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def icon(size, tile=True):
    scale = 4
    canvas = Image.new('RGBA', (size*scale, size*scale))
    draw = ImageDraw.Draw(canvas)
    factor = size*scale/256
    def box(values):
        return tuple(round(value*factor) for value in values)
    if tile:
        mask = Image.new('L', canvas.size)
        ImageDraw.Draw(mask).rounded_rectangle(box((8,8,248,248)), radius=round(56*factor), fill=255)
        gradient = Image.new('RGBA', canvas.size)
        pixels = gradient.load()
        for row in range(gradient.height):
            for column in range(gradient.width):
                fraction = min(1., (column*.35+row*.65)/gradient.height)
                pixels[column,row] = tuple(round(first+(last-first)*fraction) for first,last in zip((52,39,71),(21,19,31)))+(255,)
        canvas.paste(gradient, (0,0), mask)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(box((8,8,248,248)), radius=round(56*factor), outline='#665174', width=max(1,round(factor)))
    ring = '#B598F4' if tile else '#9B75D6'
    draw.arc(box((44,44,212,212)), 45, 315, fill=ring, width=max(1,round(12*factor)))
    radius = 84-6
    for angle in (45,315):
        center = (128+radius*math.cos(math.radians(angle)),128+radius*math.sin(math.radians(angle)))
        draw.ellipse(box((center[0]-6,center[1]-6,center[0]+6,center[1]+6)), fill=ring)
    for bounds, color in [((83,109,99,147),'#CDB9F5'),((120,83,138,173),'#F4EDFF'),((158,101,174,155),'#E0CEF9')]:
        draw.rounded_rectangle(box(bounds), radius=round((bounds[2]-bounds[0])/2*factor), fill=color if tile else ring)
    return canvas.resize((size,size), Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    master = icon(512)
    master.save(ROOT/'resources/app.png')
    master.save(ROOT/'resources/app.ico', sizes=[(size,size) for size in (16,20,24,32,40,48,64,128,256)])
    (ROOT/'web/ui/favicon.ico').write_bytes((ROOT/'resources/app.ico').read_bytes())
    master.save(ROOT/'web/ui/brand.png')
    icon(64, tile=False).save(ROOT/'web/ui/tray.png')
    master.save(args.output/'AECStudio-应用图标.png')
    (args.output/'AECStudio-应用图标.svg').write_bytes((ROOT/'resources/app.svg').read_bytes())
    (args.output/'AECStudio-应用图标.ico').write_bytes((ROOT/'resources/app.ico').read_bytes())
    sheet = Image.new('RGB', (900,480), '#111217')
    sheet.paste(icon(256), (46,55), icon(256))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 30)
    small = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 14)
    draw.text((355,67), 'AEC Studio', font=font, fill='#EEE8F8')
    draw.text((356,113), 'Voice, in focus.', font=small, fill='#A89AB8')
    for size, left in zip((16,24,32,48,64),(364,428,500,582,684)):
        image = icon(size)
        sheet.paste(image, (left,216-size//2), image)
        draw.text((left,267), str(size)+' px', font=small, fill='#9E91AE')
    for left, color in ((355,'#25202E'),(575,'#E8E5EE')):
        draw.rounded_rectangle((left,335,left+194,405), radius=14, fill=color)
        tray = icon(40, tile=False)
        sheet.paste(tray, (left+20,350), tray)
    draw.text((422,357), 'Tray', font=small, fill='#B7A6CB')
    draw.text((642,357), 'Tray', font=small, fill='#655371')
    sheet.save(args.output/'AECStudio-图标预览.png')
    with Image.open(ROOT/'resources/app.ico') as check:
        assert check.ico.sizes() == {(size,size) for size in (16,20,24,32,40,48,64,128,256)}
        for size in check.ico.sizes():
            assert check.ico.getimage(size).size == size


if __name__ == '__main__':
    main()
