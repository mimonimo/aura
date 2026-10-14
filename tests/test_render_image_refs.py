"""판독 본문의 그림 자리(![image](…))는 글자 그대로 보이지 않는다 — 그림이 있으면 그림, 없으면 「[그림]」."""
import json

from zzaimy.app import render


def _table(text):
    return json.dumps({"n_rows": 1, "n_cols": 1, "cells": [[0, 0, 1, 1, False, text]]})


def test_table_cell_image_ref_becomes_placeholder_or_image():
    html = str(render.table_html(_table("2018 개편 ![image](image_001.jpg) <b>x</b>")))
    assert "![image]" not in html and "[그림]" in html and "&lt;b&gt;" in html       # 다른 글은 그대로 이스케이프
    html = str(render.table_html(_table("![image](image_001.jpg)"), doc_id=7, assets={"image_001.jpg": 3}))
    assert '<img src="/doc/7/asset/3"' in html


def test_text_block_image_ref():
    blocks = render.chunk_blocks([{"kind": "text", "content": "본문 ![image](image_009.png) 끝", "seq": 0, "page_no": None}], 1, {})
    assert "![image]" not in "".join(map(str, blocks)) and "[그림]" in "".join(map(str, blocks))
