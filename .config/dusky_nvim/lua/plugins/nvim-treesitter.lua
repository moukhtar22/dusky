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
    -- 0.12: native highlight uses only parsers shipped with Neovim (c, lua, markdown etc).
    -- nvim-treesitter on `main` no longer provides highlight itself; it only manages parsers.
    -- We keep auto-install disabled to avoid startup network churn (seen 2s clone overhead).
    -- Install extra parsers manually when needed:
    --   :TSInstall bash python json yaml html css javascript typescript regex
    --   or uncomment below and restart:
    -- require("nvim-treesitter").install({ "bash", "python", "json", "yaml", "html", "css", "javascript", "typescript", "regex" })
  end,
}
