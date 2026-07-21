"""
Модуль извлечения текста из файлов разных форматов.
Поддерживает: PDF, DOCX, TXT, XLSX, ZIP, PNG/JPG (OCR через Tesseract).
Старые DOC — только на Windows (через pywin32). На Linux — пропускаются.
"""

import os
import sys
import tempfile
import zipfile
from pathlib import Path

# PDF
import PyPDF2
import pdfplumber

# DOCX
import docx

# XLSX
import openpyxl

# Изображения (OCR)
from PIL import Image
import pytesseract

# Только для Windows: чтение .doc через Microsoft Word
if sys.platform == "win32":
    try:
        import win32com.client
    except ImportError:
        win32com = None
else:
    win32com = None


def _extract_text_from_pdf(file_path: str) -> str:
    """Извлекает текст из PDF, пробуя pdfplumber, затем PyPDF2."""
    text = ""
    # Сначала pdfplumber (лучше сохраняет разметку и таблицы)
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception:
        pass

    if not text.strip():
        # Если pdfplumber не дал текста, пытаемся PyPDF2
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except Exception:
            pass

    return text


def _extract_text_from_docx(file_path: str) -> str:
    """Извлекает текст из DOCX (python-docx)."""
    doc = docx.Document(file_path)
    return "\n".join(paragraph.text for paragraph in doc.paragraphs)


def _extract_text_from_doc_via_com(file_path: str) -> str:
    """Только для Windows: читает .doc через Microsoft Word COM."""
    if not win32com.client:
        raise RuntimeError("pywin32 недоступен. .doc не может быть прочитан.")
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(file_path)
        text = doc.Content.Text
        doc.Close()
        return text
    finally:
        word.Quit()


def _extract_text_from_txt(file_path: str) -> str:
    """Читает обычный текстовый файл."""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _extract_text_from_xlsx(file_path: str) -> str:
    """Извлекает текст из всех ячеек Excel-файла."""
    wb = openpyxl.load_workbook(file_path, data_only=True)
    all_text = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        for row in sheet.iter_rows(values_only=True):
            row_text = " | ".join(
                str(cell) if cell is not None else "" for cell in row
            )
            if row_text.strip():
                all_text.append(row_text)
    return "\n".join(all_text)


def _ocr_image(file_path: str) -> str:
    """Распознаёт текст с изображения через Tesseract OCR."""
    img = Image.open(file_path)
    # Указываем языки (русский + английский)
    return pytesseract.image_to_string(img, lang="rus+eng")


def _extract_texts_from_zip(file_path: str) -> list[dict]:
    """
    Распаковывает ZIP-архив во временную папку и рекурсивно извлекает текст
    из всех поддерживаемых файлов внутри.
    Возвращает список словарей: {"name": имя файла, "text": текст}.
    """
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(file_path, "r") as zf:
            zf.extractall(tmpdir)

        for root, dirs, files in os.walk(tmpdir):
            for fname in files:
                full_path = os.path.join(root, fname)
                ext = Path(fname).suffix.lower()
                # Рекурсивно вызываем extract_text для каждого файла
                try:
                    text = extract_text(full_path, ext)
                    if text and text.strip():
                        results.append({"name": fname, "text": text})
                except Exception as e:
                    print(f"Ошибка при обработке файла в архиве {fname}: {e}")
    return results


def extract_text(file_path: str, file_ext: str) -> str:
    """
    Диспетчер: выбирает нужную функцию извлечения по расширению.
    Поддерживаемые расширения:
      .pdf, .docx, .txt, .xlsx, .xlsm, .png, .jpg, .jpeg, .zip
      .doc (только на Windows)
    """
    ext = file_ext.lower()

    if ext == ".pdf":
        return _extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return _extract_text_from_docx(file_path)
    elif ext == ".doc":
        if sys.platform == "win32":
            return _extract_text_from_doc_via_com(file_path)
        else:
            raise RuntimeError(
                "Файлы .doc не поддерживаются на Linux. Пожалуйста, конвертируйте их в .docx."
            )
    elif ext in (".xlsx", ".xlsm"):
        return _extract_text_from_xlsx(file_path)
    elif ext == ".txt":
        return _extract_text_from_txt(file_path)
    elif ext in (".png", ".jpg", ".jpeg"):
        return _ocr_image(file_path)
    elif ext == ".zip":
        # Для ZIP возвращаем объединённый текст всех файлов внутри
        texts = _extract_texts_from_zip(file_path)
        combined = []
        for item in texts:
            combined.append(f"=== {item['name']} ===\n{item['text']}")
        return "\n\n".join(combined)
    else:
        raise ValueError(f"Неподдерживаемый формат файла: {ext}")