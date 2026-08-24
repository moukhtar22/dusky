-- lua/plugins/noice.lua
return {
  "folke/noice.nvim",
  event = "VeryLazy",
  dependencies = {
    "MunifTanjim/nui.nvim",
    {
      "rcarriga/nvim-notify",
      opts = function()
        -- FIX: fallback to #19120c if matugen not yet loaded (prevents nil background_colour crash)
        return {
          background_colour = vim.g.base16_gui00 or "#19120c",
          render = "wrapped-compact",
          stages = "slide",
          top_down = false,
          timeout = 3000, -- reduced from 5000 for less clutter
          fps = 60,
        }
      end,
    },
  },
  opts = {
    -- CRITICAL FIX: Restored LSP routing so Noice can format documentation without warnings
    lsp = {
      override = {
        ["vim.lsp.util.convert_input_to_markdown_lines"] = true,
        ["vim.lsp.util.stylize_markdown"] = true,
        ["cmp.entry.get_documentation"] = true,
      },
    },
    presets = {
      bottom_search = true,
      command_palette = true,
      long_message_to_split = true,
      inc_rename = false,
      lsp_doc_border = false,
    },
    routes = {
      { filter = { event = "msg_show", kind = "", find = "written" }, opts = { skip = true } },
    },
    views = {
      cmdline_popup = { position = { row = 5, col = "50%" }, size = { width = 60, height = "auto" } },
      popupmenu = {
        relative = "editor",
        position = { row = 8, col = "50%" },
        size = { width = 60, height = 10 },
        border = { style = "rounded", padding = { 0, 1 } },
        win_options = { winhighlight = { Normal = "Normal", FloatBorder = "DiagnosticInfo" } },
      },
    },
  },
}
