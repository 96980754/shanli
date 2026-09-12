from yuxi.knowledge.chunking.ragflow_like.utils.table_utils import (
    html_table_to_key_value,
    html_table_to_markdown,
)


NESTED_TABLE = """
<table>
  <tr><th>Outer</th><th>Value</th></tr>
  <tr>
    <td>Before<table><tr><th>Inner</th></tr><tr><td>Nested</td></tr></table>After</td>
    <td>Peer</td>
  </tr>
</table>
"""


def test_html_table_to_markdown_flattens_nested_table_once():
    markdown = html_table_to_markdown(NESTED_TABLE)

    assert markdown.splitlines() == [
        "| Outer | Value |",
        "| --- | --- |",
        "| Before Inner; Nested After | Peer |",
    ]
    assert markdown.count("Inner") == 1
    assert markdown.count("Nested") == 1


def test_html_table_to_key_value_does_not_promote_nested_rows():
    lines = html_table_to_key_value(NESTED_TABLE)

    assert lines == ["Outer：Before Inner; Nested After；Value：Peer；"]


def test_html_table_to_markdown_keeps_rowspan_and_colspan_grid():
    html = """
    <table>
      <tr><th colspan="2">Header</th></tr>
      <tr><td rowspan="2">A</td><td>B</td></tr>
      <tr><td>C</td></tr>
    </table>
    """

    assert html_table_to_markdown(html).splitlines() == [
        "| Header | Header |",
        "| --- | --- |",
        "| A | B |",
        "| A | C |",
    ]
