-- Normalize Pandoc Table AST nodes into GFM pipe tables.
-- Complex tables are flattened because GFM pipe tables cannot represent
-- rowspan/colspan or block-level table cells directly.

local function trim(s)
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function normalize_cell_markdown(md)
  md = md or ""
  md = md:gsub("\r\n", "\n"):gsub("\r", "\n")
  md = trim(md)
  if md == "" then
    return ""
  end

  md = md:gsub("\n%s*\n+", "<br><br>")
  md = md:gsub("\n", "<br>")
  md = md:gsub("%s*<br>%s*", "<br>")
  md = md:gsub("|", "\\|")

  return md
end

local function render_blocks_as_gfm(blocks)
  if not blocks or #blocks == 0 then
    return ""
  end

  local doc = pandoc.Pandoc(blocks)
  local md = pandoc.write(doc, "gfm")
  return normalize_cell_markdown(md)
end

local function cell_text(cell)
  return render_blocks_as_gfm(cell.contents)
end

local function expand_rows(rows, num_cols)
  local grid = {}

  local function ensure_row(r)
    if not grid[r] then
      grid[r] = {}
    end
    return grid[r]
  end

  for r, row in ipairs(rows) do
    ensure_row(r)
    local c = 1

    for _, cell in ipairs(row.cells) do
      while c <= num_cols and grid[r][c] ~= nil do
        c = c + 1
      end
      if c > num_cols then
        break
      end

      local rs = cell.row_span or 1
      local cs = cell.col_span or 1

      grid[r][c] = { anchor = true, cell = cell }

      for rr = r, math.min(r + rs - 1, #rows) do
        ensure_row(rr)
        for cc = c, math.min(c + cs - 1, num_cols) do
          if not (rr == r and cc == c) then
            grid[rr][cc] = { anchor = false }
          end
        end
      end

      c = c + cs
    end
  end

  for r = 1, #rows do
    ensure_row(r)
    for c = 1, num_cols do
      if grid[r][c] == nil then
        grid[r][c] = { anchor = true, cell = { contents = {} } }
      end
    end
  end

  return grid
end

local function grid_to_text_rows(grid, num_cols)
  local rows = {}
  for r = 1, #grid do
    local row = {}
    for c = 1, num_cols do
      local slot = grid[r][c]
      if slot.anchor and slot.cell then
        row[c] = cell_text(slot.cell)
      else
        row[c] = ""
      end
    end
    rows[#rows + 1] = row
  end
  return rows
end

local function merge_head_rows(head_rows, num_cols)
  if #head_rows == 0 then
    local blank = {}
    for c = 1, num_cols do
      blank[c] = ""
    end
    return blank
  end

  local merged = {}
  for c = 1, num_cols do
    local parts = {}
    for r = 1, #head_rows do
      local txt = head_rows[r][c] or ""
      if txt ~= "" then
        parts[#parts + 1] = txt
      end
    end
    merged[c] = table.concat(parts, "<br>")
  end
  return merged
end

local function alignment_marker(alignment)
  if alignment == "AlignLeft" then
    return ":---"
  elseif alignment == "AlignRight" then
    return "---:"
  elseif alignment == "AlignCenter" then
    return ":--:"
  else
    return "---"
  end
end

local function render_pipe_row(cells)
  return "| " .. table.concat(cells, " | ") .. " |"
end

local function caption_to_markdown(caption)
  if not caption or not caption.long or #caption.long == 0 then
    return ""
  end

  local text = render_blocks_as_gfm(caption.long)
  if text == "" then
    return ""
  end
  return "Table: " .. text
end

local function collect_body_rows(tbl, num_cols)
  local rows = {}
  for _, body in ipairs(tbl.bodies) do
    local body_rows = grid_to_text_rows(expand_rows(body.body, num_cols), num_cols)
    for _, row in ipairs(body_rows) do
      rows[#rows + 1] = row
    end
  end
  return rows
end

local function collect_foot_rows(tbl, num_cols)
  if not tbl.foot or not tbl.foot.rows or #tbl.foot.rows == 0 then
    return {}
  end
  return grid_to_text_rows(expand_rows(tbl.foot.rows, num_cols), num_cols)
end

function Table(tbl)
  local num_cols = #tbl.colspecs
  if num_cols == 0 then
    return nil
  end

  local head_grid = expand_rows(tbl.head.rows or {}, num_cols)
  local head_rows = grid_to_text_rows(head_grid, num_cols)
  local header = merge_head_rows(head_rows, num_cols)

  local body_rows = collect_body_rows(tbl, num_cols)
  local foot_rows = collect_foot_rows(tbl, num_cols)

  for _, row in ipairs(foot_rows) do
    body_rows[#body_rows + 1] = row
  end

  if #body_rows == 0 then
    local blank = {}
    for c = 1, num_cols do
      blank[c] = ""
    end
    body_rows[1] = blank
  end

  local lines = {}
  local caption = caption_to_markdown(tbl.caption)
  if caption ~= "" then
    lines[#lines + 1] = caption
    lines[#lines + 1] = ""
  end

  lines[#lines + 1] = render_pipe_row(header)

  local markers = {}
  for i, colspec in ipairs(tbl.colspecs) do
    markers[i] = alignment_marker(colspec[1])
  end
  lines[#lines + 1] = render_pipe_row(markers)

  for _, row in ipairs(body_rows) do
    lines[#lines + 1] = render_pipe_row(row)
  end

  return pandoc.RawBlock("markdown", "\n" .. table.concat(lines, "\n") .. "\n")
end
