import re
from bs4 import BeautifulSoup

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\x00-\x7F]+", "", text)
    return text.strip()