import fitz
import pytest

from yuxi.knowledge.flowchart_render import FlowchartRenderError, render_flowchart_pdf


def make_pdf(*, pages=1, width=220, height=180):
    document = fitz.open()
    for number in range(pages):
        page = document.new_page(width=width, height=height)
        page.insert_text((20, 30), f"Page {number + 1}")
    result = document.tobytes()
    document.close()
    return result


def test_valid_pdf_renders_ordered_rgb_png_pages(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_render_dpi", 280)
    images = render_flowchart_pdf(make_pdf(pages=3))

    assert [image.page_number for image in images] == [1, 2, 3]
    assert all(image.role == "global" and image.crop_box is None for image in images)
    assert all(image.png_bytes.startswith(b"\x89PNG\r\n") for image in images)
    assert images[0].width == round(220 * 280 / 72)
    assert images[0].height == round(180 * 280 / 72)
    assert images[0].requested_dpi == images[0].effective_dpi == 280
    assert "FLOWCHART_RENDER_DPI_REDUCED" not in images[0].warnings


def test_large_page_reduces_dpi_before_render(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_render_dpi", 280)
    monkeypatch.setattr(flowchart_render.config, "flowchart_min_render_dpi", 72)
    monkeypatch.setattr(flowchart_render.config, "flowchart_max_image_pixels", 1_000_000)

    image = render_flowchart_pdf(make_pdf(width=500, height=500))[0]

    assert image.requested_dpi == 280
    assert image.effective_dpi == 144
    assert image.width * image.height <= 1_000_000
    assert "FLOWCHART_RENDER_DPI_REDUCED" in image.warnings


def test_min_dpi_boundary_renders_when_pixels_fit(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_min_render_dpi", 72)
    monkeypatch.setattr(flowchart_render.config, "flowchart_max_image_pixels", 250_000)

    image = render_flowchart_pdf(make_pdf(width=500, height=500))[0]

    assert image.effective_dpi == 72
    assert image.width * image.height <= 250_000
    assert "FLOWCHART_COMPLEX_PAGE" in image.warnings


def test_huge_page_rejects_if_min_dpi_exceeds_pixel_limit(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_min_render_dpi", 72)
    monkeypatch.setattr(flowchart_render.config, "flowchart_max_image_pixels", 1_000_000)

    with pytest.raises(FlowchartRenderError) as error:
        render_flowchart_pdf(make_pdf(width=5000, height=5000))

    assert error.value.code == "FLOWCHART_IMAGE_TOO_LARGE"
    assert "最低 DPI" in str(error.value)


@pytest.mark.parametrize("data", [b"not a pdf", b"%PDF-corrupt"])
def test_invalid_or_corrupt_pdf_is_rejected(data):
    with pytest.raises(FlowchartRenderError) as error:
        render_flowchart_pdf(data)
    assert error.value.code == "INVALID_FLOWCHART_PDF"


def test_too_many_pages_is_rejected_before_render(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_max_pages", 1)
    with pytest.raises(FlowchartRenderError) as error:
        render_flowchart_pdf(make_pdf(pages=2))
    assert error.value.code == "FLOWCHART_TOO_MANY_PAGES"


def test_extreme_page_records_warning_without_cropping(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_max_image_pixels", 30_000_000)
    image = render_flowchart_pdf(make_pdf(width=720, height=90))[0]
    assert "FLOWCHART_EXTREME_ASPECT_RATIO" in image.warnings
    assert image.role == "global"
    assert image.crop_box is None


def test_pixel_limit_rejects_page_before_pixmap(monkeypatch):
    from yuxi.knowledge import flowchart_render

    monkeypatch.setattr(flowchart_render.config, "flowchart_max_image_pixels", 100)
    with pytest.raises(FlowchartRenderError) as error:
        render_flowchart_pdf(make_pdf())
    assert error.value.code == "FLOWCHART_IMAGE_TOO_LARGE"
