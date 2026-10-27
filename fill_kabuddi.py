#!/usr/bin/env python3
import os

target = 42 * 1024 * 1024  # 42 MB
word = "Кабудди-буда\n"
chunk = word * 1000

with open("kabuddi-buda.txt", "w", encoding="utf-8") as f:
    written = 0
    while written < target:
        f.write(chunk)
        written = f.tell()

size = os.path.getsize("kabuddi-buda.txt")
print(f"Готово! Размер файла: {size / 1024 / 1024:.2f} МБ")
