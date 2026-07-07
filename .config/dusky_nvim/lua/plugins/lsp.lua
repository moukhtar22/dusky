-- lua/plugins/lsp.lua
-- Native LSP configuration for Neovim 0.12+

return {
  {
    "williamboman/mason.nvim",
    cmd = { "Mason", "MasonInstall", "MasonUninstall", "MasonUpdate" },
    opts = {
      ensure_installed = {
        "lua-language-server",
        "pyright",
        "bash-language-server",
      },
    },
    config = function(_, opts)
      require("mason").setup(opts)
      -- Prepend mason path to system PATH so native LSP can spawn them
      vim.env.PATH = vim.fn.stdpath("data") .. "/mason/bin:" .. vim.env.PATH
      
      -- Auto install the packages defined in ensure_installed
      local registry = require("mason-registry")
      registry.refresh(function()
        for _, pkg_name in ipairs(opts.ensure_installed) do
          if not registry.is_installed(pkg_name) then
            local pkg = registry.get_package(pkg_name)
            pkg:install()
          end
        end
      end)
    end,
  },
  {
    "neovim/nvim-lspconfig",
    event = { "BufReadPre", "BufNewFile" },
    dependencies = { "williamboman/mason.nvim" },
    config = function()
      -- Configure diagnostic display options
      vim.diagnostic.config({
        virtual_text = true,
        signs = true,
        update_in_insert = false,
        severity_sort = true,
      })

      -- Global mappings for LSP
      vim.api.nvim_create_autocmd("LspAttach", {
        group = vim.api.nvim_create_augroup("UserLspConfig", {}),
        callback = function(ev)
          local opts = { buffer = ev.buf }
          vim.keymap.set("n", "gD", vim.lsp.buf.declaration, vim.tbl_extend("force", opts, { desc = "Go to declaration" }))
          vim.keymap.set("n", "gd", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "Go to definition" }))
          vim.keymap.set("n", "K", vim.lsp.buf.hover, vim.tbl_extend("force", opts, { desc = "Hover docs" }))
          vim.keymap.set("n", "gi", vim.lsp.buf.implementation, vim.tbl_extend("force", opts, { desc = "Go to implementation" }))
          vim.keymap.set("n", "<C-k>", vim.lsp.buf.signature_help, vim.tbl_extend("force", opts, { desc = "Signature help" }))
          vim.keymap.set("n", "<leader>wa", vim.lsp.buf.add_workspace_folder, vim.tbl_extend("force", opts, { desc = "Add workspace folder" }))
          vim.keymap.set("n", "<leader>wr", vim.lsp.buf.remove_workspace_folder, vim.tbl_extend("force", opts, { desc = "Remove workspace folder" }))
          vim.keymap.set("n", "<leader>wl", function()
            print(vim.inspect(vim.lsp.buf.list_workspace_folders()))
          end, vim.tbl_extend("force", opts, { desc = "List workspace folders" }))
          vim.keymap.set("n", "<leader>D", vim.lsp.buf.type_definition, vim.tbl_extend("force", opts, { desc = "Type definition" }))
          vim.keymap.set("n", "<leader>rn", vim.lsp.buf.rename, vim.tbl_extend("force", opts, { desc = "Rename symbol" }))
          vim.keymap.set({ "n", "v" }, "<leader>ca", vim.lsp.buf.code_action, vim.tbl_extend("force", opts, { desc = "Code actions" }))
          vim.keymap.set("n", "gr", vim.lsp.buf.references, vim.tbl_extend("force", opts, { desc = "Show references" }))
        end,
      })

      -- Define configurations for preferred language servers
      local servers = {
        -- Lua LSP config
        lua_ls = {
          settings = {
            Lua = {
              diagnostics = {
                globals = { "vim" },
              },
              workspace = {
                checkThirdParty = false,
                library = vim.api.nvim_get_runtime_file("", true),
              },
              telemetry = { enable = false },
            },
          },
        },
        -- Python LSP config
        pyright = {},
        -- Bash LSP config
        bashls = {},
      }

      -- Enable each language server natively
      for name, config in pairs(servers) do
        vim.lsp.config(name, config)
        vim.lsp.enable(name)
      end
    end,
  },
}
