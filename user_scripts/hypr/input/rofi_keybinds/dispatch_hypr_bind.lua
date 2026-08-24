#!/usr/bin/env luajit
-- ==============================================================================
-- Hyprland 0.56+ Keybinding Dispatch Helper
-- Resolves keybinding descriptions from ~/.config/hypr to exact actions
-- ==============================================================================

local target_desc = arg[1] or ""
local fallback_dsp = arg[2] or ""
local fallback_arg = arg[3] or ""

-- If fallback is direct exec or exec_cmd with non-empty arg, run immediately
if (fallback_dsp == "exec" or fallback_dsp == "exec_cmd") and fallback_arg ~= "" then
    os.execute(fallback_arg .. " >/dev/null 2>&1 &")
    os.exit(0)
end

local HOME = os.getenv("HOME") or ("/home/" .. (os.getenv("USER") or ""))
dusky_scripts = HOME .. "/user_scripts/"

-- Include Hyprland config dir in package path dynamically
package.path = HOME .. "/.config/hypr/?.lua;" ..
               HOME .. "/.config/hypr/?/init.lua;" ..
               HOME .. "/.config/hypr/source/?.lua;" ..
               HOME .. "/.config/hypr/edit_here/source/?.lua;" ..
               package.path

local binds = {}

local function make_dsp_proxy(path)
    return setmetatable({}, {
        __index = function(_, k)
            return make_dsp_proxy(path .. "." .. k)
        end,
        __call = function(_, ...)
            return { _type = "dsp", path = path, args = {...} }
        end
    })
end

hl = {}
hl.dsp = setmetatable({}, {
    __index = function(_, k)
        return make_dsp_proxy("hl.dsp." .. k)
    end
})

function hl.bind(key, dsp, opts)
    if opts and opts.description then
        binds[opts.description] = dsp
    end
end

function hl.config() end
function hl.get_config() return 1.0 end
function hl.define_submap(name, fn)
    if type(fn) == "function" then
        pcall(fn)
    end
end
function hl.get_active_window() return nil end

function cond_bind(key, default_dsp, flags)
    if flags and flags.description then
        binds[flags.description] = default_dsp
    end
end

-- Load Hyprland configuration files
pcall(require, "edit_here.source.default_apps")
pcall(require, "source.keybinds")
pcall(require, "edit_here.source.keybinds")

local action = binds[target_desc]

if type(action) == "table" and action._type == "dsp" then
    if action.path == "hl.dsp.exec_cmd" and action.args[1] then
        local cmd = tostring(action.args[1])
        os.execute(cmd .. " >/dev/null 2>&1 &")
        os.exit(0)
    else
        -- Build Lua dispatcher call string e.g. hl.dsp.window.close()
        local lua_call = action.path .. "("
        local arg_strs = {}
        for _, a in ipairs(action.args) do
            if type(a) == "string" then
                table.insert(arg_strs, string.format("%q", a))
            elseif type(a) == "number" or type(a) == "boolean" then
                table.insert(arg_strs, tostring(a))
            elseif type(a) == "table" then
                local kv = {}
                for k, v in pairs(a) do
                    if type(v) == "string" then
                        table.insert(kv, string.format("%s = %q", k, v))
                    else
                        table.insert(kv, string.format("%s = %s", k, tostring(v)))
                    end
                end
                table.insert(arg_strs, "{ " .. table.concat(kv, ", ") .. " }")
            end
        end
        lua_call = lua_call .. table.concat(arg_strs, ", ") .. ")"
        
        local cmd = "hyprctl dispatch " .. string.format("%q", lua_call)
        local res = os.execute(cmd)
        os.exit(res == 0 and 0 or 1)
    end
elseif type(action) == "function" then
    if fallback_dsp ~= "" and fallback_dsp ~= "__lua" then
        local cmd = "hyprctl dispatch " .. string.format("%q", fallback_dsp)
        if fallback_arg ~= "" then
            cmd = cmd .. " " .. string.format("%q", fallback_arg)
        end
        local res = os.execute(cmd)
        os.exit(res == 0 and 0 or 1)
    end
    os.exit(0)
end

-- Fallback to standard hyprctl dispatch
if fallback_dsp ~= "" and fallback_dsp ~= "__lua" then
    local cmd = "hyprctl dispatch " .. string.format("%q", fallback_dsp)
    if fallback_arg ~= "" then
        cmd = cmd .. " " .. string.format("%q", fallback_arg)
    end
    local res = os.execute(cmd)
    os.exit(res == 0 and 0 or 1)
end
