from yuxi.knowledge.parser.zip_utils import replace_image_links


def make_images():
    return [
        {
            "name": "img_in_image_box_117_68_187_138.jpg",
            "url": "http://localhost:9000/public/kb_x/kb-images/1_img_in_image_box_117_68_187_138.jpg",
            "path": "images/img_in_image_box_117_68_187_138.jpg",
        },
        {
            "name": "figure_1.png",
            "url": "http://localhost:9000/public/kb_x/kb-images/2_figure_1.png",
            "path": "images/figure_1.png",
        },
    ]


def test_rewrites_markdown_image_links_by_basename():
    markdown = "前文\n\n![figure_1](imgs/figure_1.png)\n\n后文"
    result = replace_image_links(markdown, make_images())
    assert result == "前文\n\n![figure_1](http://localhost:9000/public/kb_x/kb-images/2_figure_1.png)\n\n后文"


def test_rewrites_html_img_src_keeping_other_attributes():
    # MinerU 部分输出用 HTML img + 相对路径 imgs/ 引用图片（裂图根因）
    markdown = '<div style="text-align: center;"><img src="imgs/img_in_image_box_117_68_187_138.jpg" alt="Image" width="5%" /></div>'
    result = replace_image_links(markdown, make_images())
    assert (
        result
        == '<div style="text-align: center;"><img src="http://localhost:9000/public/kb_x/kb-images/1_img_in_image_box_117_68_187_138.jpg" alt="Image" width="5%" /></div>'
    )


def test_leaves_unknown_image_references_untouched():
    markdown = "![missing](imgs/missing.jpg)\n\n<img src=\"imgs/missing.jpg\" alt=\"Image\" />"
    result = replace_image_links(markdown, make_images())
    assert result == markdown


def test_returns_content_unchanged_without_images():
    markdown = '<img src="imgs/a.jpg" />'
    assert replace_image_links(markdown, []) == markdown
