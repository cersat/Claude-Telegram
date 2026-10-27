"""Конвертер markdown -> Telegram HTML."""

import re


def markdown_to_html(text):
    """Конвертирует Markdown-разметку в Telegram HTML (parse_mode=HTML).
    Поддерживает: *bold*, _italic_, `code`, ```blocks```, ||spoiler||,
    [text](url), > blockquote, markdown-таблицы, ~~strike~~, __underline__.
    """
    def tg_escape(s):
        """Эскейп только символов, которые поддерживает Telegram HTML."""
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 1. Сохраняем fenced code blocks
    code_blocks = []
    def save_fenced(m):
        lang = (m.group(1) or '').strip()
        code = tg_escape(m.group(2))
        if lang:
            code_blocks.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
        else:
            code_blocks.append(f'<pre><code>{code}</code></pre>')
        return f'\x00BLOCK{len(code_blocks)-1}\x00'
    text = re.sub(r'```(\w*)\n?([\s\S]*?)```', save_fenced, text)

    # 2. Сохраняем inline code
    inline_codes = []
    def save_inline(m):
        inline_codes.append(f'<code>{tg_escape(m.group(1))}</code>')
        return f'\x00INLINE{len(inline_codes)-1}\x00'
    text = re.sub(r'`([^`\n]+)`', save_inline, text)

    # 3. Эскейпим HTML-спецсимволы (после сохранения code blocks)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 3б. Защищаем ссылки от разметки: в URL полно _ и * (client_id, redirect_uri, state=…),
    # которые иначе становились курсивом/жирным и ломали ссылку.
    urls = []
    def save_url(m):
        full = m.group(0)
        tail = ''
        while full and full[-1] in '*_~.,;:!?)]':  # знаки после ссылки — не часть URL
            # закрывающая ")" — часть адреса, если у неё есть парная "(" (…/Foo_(bar)); иначе это скобка текста/ссылки
            if full[-1] == ')' and full.count('(') >= full.count(')'):
                break
            tail = full[-1] + tail
            full = full[:-1]
        urls.append(full)
        return f'\x00URL{len(urls)-1}\x00' + tail
    text = re.sub(r'(?:https?|tg|mailto):(?://)?[^\s<>"\x00]+', save_url, text)

    # 4. Markdown таблицы → HTML <table> (Telegram поддерживает нативные таблицы)
    def replace_table(m):
        raw = m.group(0)
        lines = [l.strip() for l in raw.strip().split('\n') if l.strip()]
        header = None
        data_rows = []
        for line in lines:
            if re.match(r'^\|[\s\-:|]+\|$', line):
                continue  # разделительная строка
            cells = [c.strip() for c in line.strip('|').split('|')]
            if header is None:
                header = cells
            else:
                data_rows.append(cells)
        if not header:
            return raw
        col_count = max(len(header), max((len(r) for r in data_rows), default=0))
        while len(header) < col_count:
            header.append('')
        for r in data_rows:
            while len(r) < col_count:
                r.append('')
        th_cells = ''.join(f'<th>{c}</th>' for c in header)
        thead = f'<thead><tr>{th_cells}</tr></thead>'
        tbody_rows = ''
        for row in data_rows:
            td_cells = ''.join(f'<td>{c}</td>' for c in row)
            tbody_rows += f'<tr>{td_cells}</tr>'
        tbody = f'<tbody>{tbody_rows}</tbody>'
        return f'<table>{thead}{tbody}</table>'
    # Таблицы: строки начинающиеся с | но НЕ с || (чтобы не перепутать со спойлерами)
    text = re.sub(r'(?:^\|[^|\n][^\n]*\n)*^\|[^|\n][^\n]*', replace_table, text, flags=re.MULTILINE)

    # 5. Убираем --- на отдельной строке
    text = re.sub(r'^\s*---+\s*$', '', text, flags=re.MULTILINE)

    # 6. Blockquotes: > text → <blockquote>text</blockquote>
    def replace_blockquote(m):
        content = re.sub(r'^&gt;\s?', '', m.group(0), flags=re.MULTILINE)
        return f'<blockquote>{content.strip()}</blockquote>'
    text = re.sub(r'(?:^&gt;.*\n?)+', replace_blockquote, text, flags=re.MULTILINE)

    # 7. Спойлеры: ||text|| → <tg-spoiler>text</tg-spoiler>
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)

    # 8. Форматирование текста
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)   # **bold**
    text = re.sub(r'\*([^\s*][^*\n]*?[^\s*]|[^\s*])\*', r'<b>\1</b>', text)  # *bold*
    text = re.sub(r'(?<!\w)__(.+?)__(?!\w)', r'<u>\1</u>', text)         # __underline__
    text = re.sub(r'(?<!\w)_([^\s_][^_\n]*?[^\s_]|[^\s_])_(?!\w)', r'<i>\1</i>', text)  # _italic_
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)         # ~~strike~~
    text = re.sub(r'\[(.+?)\]\(((?:[^()\s]|\([^()\s]*\))+)\)', r'<a href="\2">\1</a>', text)  # [text](url), в url допустимы пары скобок

    # 9. Восстанавливаем code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00BLOCK{i}\x00', block)
    for i, code in enumerate(inline_codes):
        text = text.replace(f'\x00INLINE{i}\x00', code)
    for i, u in enumerate(urls):
        text = text.replace(f'\x00URL{i}\x00', u)

    return text

# Псевдоним для совместимости
def fix_markdown(text):
    return markdown_to_html(text)
