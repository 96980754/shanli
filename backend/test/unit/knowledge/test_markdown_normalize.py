"""解析结果 Markdown 出口级规整测试。"""

from yuxi.knowledge.parser.markdown_normalize import (
    find_dangling_image_refs,
    strip_image_reference,
    strip_presentational_html,
)


def test_strips_table_style_and_border_keeping_span_attributes():
    table = (
        "<table border=1 style='margin: auto; word-wrap: break-word;'>"
        '<tr><td colspan="3" rowspan="2" style=\'text-align: center;\'>标题</td></tr></table>'
    )

    result = strip_presentational_html(table)

    assert result == '<table><tr><td colspan="3" rowspan="2">标题</td></tr></table>'


def test_strips_double_quoted_style_and_border():
    table = '<table border="1" style="width: 100%;"><thead><th style="text-align: left;">列</th></thead></table>'

    assert strip_presentational_html(table) == "<table><thead><th>列</th></thead></table>"


def test_keeps_style_on_non_table_tags():
    # 图片容器的 text-align 是引擎唯一的居中手段，剥离会让预览布局变化
    markdown = '<div style="text-align: center;"><img src="http://minio/public/a.jpg" style="width: 50%;" /></div>'

    assert strip_presentational_html(markdown) == markdown


def test_strip_image_reference_removes_html_img_and_its_wrapper():
    text = (
        '前文\n\n<div style="text-align: center;">'
        '<img src="imgs/img_in_seal_box_1_2_3_4.jpg" alt="Image" /></div>\n\n后文'
    )

    result = strip_image_reference(text, "imgs/img_in_seal_box_1_2_3_4.jpg")

    assert result == "前文\n\n后文"


def test_strip_image_reference_removes_markdown_image_link():
    text = "前文\n\n![Image](imgs/figure_1.jpg)\n\n后文"

    assert strip_image_reference(text, "imgs/figure_1.jpg") == "前文\n\n后文"


def test_strip_image_reference_leaves_other_images_alone():
    text = '<img src="imgs/keep.jpg" alt="Image" />'

    assert strip_image_reference(text, "imgs/drop.jpg") == text


def test_find_dangling_image_refs_reports_relative_paths_only():
    markdown = (
        '<img src="imgs/img_in_image_box_1_2_3_4.jpg" alt="Image" />\n\n'
        "![图](http://localhost:9000/public/kb/kb-images/1_a.jpg)\n\n"
        "![](imgs/figure_1.jpg)\n\n"
        "![](data:image/png;base64,AAAA)"
    )

    assert find_dangling_image_refs(markdown) == ["imgs/img_in_image_box_1_2_3_4.jpg", "imgs/figure_1.jpg"]


def test_find_dangling_image_refs_returns_empty_for_renderable_markdown():
    markdown = '<div style="text-align: center;"><img src="http://localhost:9000/public/kb/kb-images/1_a.jpg" /></div>'

    assert find_dangling_image_refs(markdown) == []
