from __future__ import annotations

from bs4 import BeautifulSoup


def _direct_rows(table):
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _direct_cells(row):
    return [cell for cell in row.find_all(["td", "th"]) if cell.find_parent("tr") is row]


def _cell_text(cell) -> str:
    for nested_table in reversed(cell.find_all("table")):
        rows = []
        for row in _direct_rows(nested_table):
            values = [_cell_text(nested_cell) for nested_cell in _direct_cells(row)]
            if any(values):
                rows.append(" / ".join(values))
        nested_table.replace_with("; ".join(rows))
    return cell.get_text(" ", strip=True).replace("\n", " ").replace("|", "\\|")


def _build_grid(html: str) -> list[list[str | None]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    rows = _direct_rows(table)
    if not rows:
        return []

    grid: list[list[str | None]] = []
    for r_idx, row in enumerate(rows):
        while len(grid) <= r_idx:
            grid.append([])

        c_idx = 0
        for cell in _direct_cells(row):
            while c_idx < len(grid[r_idx]) and grid[r_idx][c_idx] is not None:
                c_idx += 1

            text = _cell_text(cell)
            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))

            for r_offset in range(rowspan):
                target_r = r_idx + r_offset
                while len(grid) <= target_r:
                    grid.append([])
                for c_offset in range(colspan):
                    target_c = c_idx + c_offset
                    while len(grid[target_r]) <= target_c:
                        grid[target_r].append(None)
                    grid[target_r][target_c] = text
            c_idx += colspan

    return grid


def html_table_to_markdown(html: str) -> str:
    """将 HTML 表格转换为 Markdown，嵌套表格压平到所属单元格。"""
    grid = _build_grid(html)
    if not grid:
        return ""

    max_cols = max(len(row) for row in grid)
    normalized = [
        [(cell or "") for cell in row] + [""] * (max_cols - len(row))
        for row in grid
    ]
    markdown_lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "|" + "|".join([" --- " for _ in range(max_cols)]) + "|",
    ]
    markdown_lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(markdown_lines)


def html_table_to_key_value(html: str) -> list[str]:
    """将 HTML 表格转换为按表头组织的键值行。"""
    grid = _build_grid(html)
    if not grid:
        return []

    headers = [header or "" for header in grid[0]]
    output = []
    for row in grid[1:]:
        row_parts = []
        for index, key in enumerate(headers):
            if key:
                value = row[index] if index < len(row) and row[index] is not None else ""
                row_parts.append(f"{key}：{value}")
        if row_parts:
            output.append("；".join(row_parts) + "；")
    return output
