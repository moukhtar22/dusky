-- ================================================================================================
-- TITLE : nvim-treesitter
-- ABOUT : Treesitter configurations and abstraction layer for Neovim.
-- ================================================================================================

return {
  "nvim-treesitter/nvim-treesitter",
  branch = "main", -- CRITICAL FIX: The master branch is deprecated and breaks in Neovim 0.12
  build = ":TSUpdate",
  event = { "BufReadPost", "BufNewFile" },
  lazy = vim.fn.argc(-1) == 0, 
  config = function()
    -- 1. Parser Management (The only remaining role of nvim-treesitter on the main branch)
    require("nvim-treesitter").install({
      "bash", "c", "cpp", "css", "dockerfile", "go", "html", "javascript",
      "json", "lua", "markdown", "markdown_inline", "python", "query",
      "regex", "rust", "svelte", "typescript", "vim", "vimdoc", "vue", "yaml",
    })

    -- NOTE: Folding is already correctly configured natively in options.lua!
  end,
}
