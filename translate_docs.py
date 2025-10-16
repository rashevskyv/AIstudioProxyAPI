import pathlib
import re
import time
from googletrans import Translator

DOC_DIR = pathlib.Path('docs')
FILES = sorted(DOC_DIR.glob('*.md'))
translator = Translator()

def translate_line(line):
    if not line.strip():
        return line
    for attempt in range(5):
        try:
            return translator.translate(line, src='zh-cn', dest='en').text
        except Exception:
            time.sleep(0.5 + attempt * 0.5)
    return line

for path in FILES:
    lines = path.read_text(encoding='utf-8').splitlines()
    in_code = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('`'):
            in_code = not in_code
            new_lines.append(line)
            continue
        if not in_code and re.search(r'[\u4e00-\u9fff]', line):
            new_lines.append(translate_line(line))
            time.sleep(0.05)
        else:
            new_lines.append(line)
    path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
