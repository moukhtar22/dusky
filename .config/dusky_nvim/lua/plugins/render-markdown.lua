return {
  "MeanderingProgrammer/render-markdown.nvim",
  ft = { "markdown" },
  dependencies = {
    "nvim-treesitter/nvim-treesitter",
    "nvim-tree/nvim-web-devicons",
  },
  opts = {
    heading = {
      sign = false,
      icons = { "◉ ", "○ ", "✸ ", "✿ ", "✦ ", "✧ " },
    },
    checkbox = {
      enabled = true,
      unchecked = { icon = "󰄱 " },
      checked = { icon = "󰄵 " },
    },
    code = {
      sign = false,
      width = "block",
      right_pad = 4,
    },
  },
}
