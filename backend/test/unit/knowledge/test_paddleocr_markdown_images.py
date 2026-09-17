"""PaddleOCR-VL markdown 图片引用改写测试（HTML img 裂图修复）。"""

from yuxi.knowledge.parser.paddleocr_api import PaddleOCRVLParser


def make_parser(monkeypatch, upload_map: dict[str, str]):
    """构造不做网络上传的 parser，_upload_markdown_image 按路径返回固定 URL。"""
    parser = PaddleOCRVLParser(api_token="test-token")
    monkeypatch.setattr(
        parser,
        "_upload_markdown_image",
        lambda image_url, image_path, params: upload_map[image_path],
    )
    return parser


def make_rows(text: str, images: dict[str, str]) -> list[dict]:
    # PaddleOCR jobs 接口的 layoutParsingResults 结构
    return [{"result": {"layoutParsingResults": [{"markdown": {"text": text, "images": images}}]}}]


def test_rewrites_html_img_src_keeping_other_attributes(monkeypatch):
    # 裂图根因：PaddleOCR-VL 输出用 HTML img + 相对路径 imgs/ 引用图片
    text = (
        '<div style="text-align: center;"><img src="imgs/img_in_image_box_238_26_573_276.jpg" '
        'alt="Image" width="41%" /></div>\n\nPOCSTARS Technology Co., Ltd.'
    )
    parser = make_parser(
        monkeypatch,
        {"imgs/img_in_image_box_238_26_573_276.jpg": "http://localhost:9000/public/kb_x/kb-images/1_img.jpg"},
    )
    rows = make_rows(text, {"imgs/img_in_image_box_238_26_573_276.jpg": "https://remote/img.jpg"})

    result = parser._extract_markdown(rows, {})

    assert (
        result
        == '<div style="text-align: center;"><img src="http://localhost:9000/public/kb_x/kb-images/1_img.jpg" '
        'alt="Image" width="41%" /></div>\n\nPOCSTARS Technology Co., Ltd.'
    )


def test_rewrites_markdown_image_links_as_before(monkeypatch):
    text = "前文\n\n![Image](imgs/figure_1.jpg)\n\n后文"
    parser = make_parser(monkeypatch, {"imgs/figure_1.jpg": "http://localhost:9000/public/kb_x/kb-images/2_figure_1.jpg"})
    rows = make_rows(text, {"imgs/figure_1.jpg": "https://remote/figure_1.jpg"})

    result = parser._extract_markdown(rows, {})

    assert result == "前文\n\n![Image](http://localhost:9000/public/kb_x/kb-images/2_figure_1.jpg)\n\n后文"


def test_rewrites_html_img_with_single_quotes(monkeypatch):
    text = "<img src='imgs/a.b.jpg' alt='Image' />"
    parser = make_parser(monkeypatch, {"imgs/a.b.jpg": "http://localhost:9000/public/kb_x/kb-images/3_a.jpg"})
    rows = make_rows(text, {"imgs/a.b.jpg": "https://remote/a.jpg"})

    result = parser._extract_markdown(rows, {})

    assert result == "<img src='http://localhost:9000/public/kb_x/kb-images/3_a.jpg' alt='Image' />"


def test_leaves_unknown_html_img_untouched(monkeypatch):
    text = '<img src="imgs/missing.jpg" alt="Image" />'
    parser = make_parser(monkeypatch, {"imgs/other.jpg": "http://localhost:9000/public/kb_x/kb-images/4_other.jpg"})
    rows = make_rows(text, {"imgs/other.jpg": "https://remote/other.jpg"})

    result = parser._extract_markdown(rows, {})

    assert result == text


def test_drop_layout_crops_removes_html_img_crop_without_uploading(monkeypatch):
    # 图片输入：碎片整体丢弃，make_parser 的上传映射取不到 key 就会 KeyError
    text = (
        '善理通益信息科技（深圳）有限公司\n\n<div style="text-align: center;">'
        '<img src="imgs/img_in_seal_box_1_2_3_4.jpg" alt="Image" width="9%" /></div>'
    )
    parser = make_parser(monkeypatch, {})
    rows = make_rows(text, {"imgs/img_in_seal_box_1_2_3_4.jpg": "https://remote/seal.jpg"})

    result = parser._extract_markdown(rows, {}, drop_layout_crops=True)

    assert result == "善理通益信息科技（深圳）有限公司"


def test_drop_layout_crops_removes_markdown_image_link(monkeypatch):
    text = "前文\n\n![Image](imgs/figure_1.jpg)\n\n后文"
    parser = make_parser(monkeypatch, {})
    rows = make_rows(text, {"imgs/figure_1.jpg": "https://remote/figure_1.jpg"})

    result = parser._extract_markdown(rows, {}, drop_layout_crops=True)

    assert result == "前文\n\n后文"
