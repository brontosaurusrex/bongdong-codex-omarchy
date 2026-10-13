-- Codex working border: choose a vivid color from the current Omarchy palette.
-- Omarchy reloads Hyprland on theme changes, so the color follows the theme.
-- Both named palettes and older color0..color15 palettes are supported.
local codex_palette = {}
local codex_palette_file = io.open(os.getenv("HOME") .. "/.local/state/omarchy/current/theme/colors.toml", "r")
if codex_palette_file then
    for line in codex_palette_file:lines() do
        local name, hex = line:match('^%s*([%w_]+)%s*=%s*["\']#(%x%x%x%x%x%x)["\']')
        if name then codex_palette[name] = hex end
    end
    codex_palette_file:close()
end

local function codex_rgb(hex)
    return tonumber(hex:sub(1, 2), 16) / 255,
        tonumber(hex:sub(3, 4), 16) / 255,
        tonumber(hex:sub(5, 6), 16) / 255
end

local function codex_brightness(r, g, b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
end

local function codex_hue(r, g, b)
    local maximum, minimum = math.max(r, g, b), math.min(r, g, b)
    local chroma = maximum - minimum
    if chroma < 0.05 then return nil end
    if maximum == r then return ((g - b) / chroma) % 6 / 6 end
    if maximum == g then return ((b - r) / chroma + 2) / 6 end
    return ((r - g) / chroma + 4) / 6
end

local background_r, background_g, background_b = codex_rgb(codex_palette.background or "000000")
local background_brightness = codex_brightness(background_r, background_g, background_b)
local accent_r, accent_g, accent_b = codex_rgb(codex_palette.accent or codex_palette.foreground or "ffffff")
local accent_hue = codex_hue(accent_r, accent_g, accent_b)
local codex_color_keys = {
    "red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown",
    "bright_red", "bright_yellow", "bright_green", "bright_cyan", "bright_blue", "bright_magenta",
}
for index = 0, 15 do
    codex_color_keys[#codex_color_keys + 1] = "color" .. index
end

local codex_working_hex, codex_best_score = nil, -1
for _, key in ipairs(codex_color_keys) do
    local hex = codex_palette[key]
    if hex then
        local r, g, b = codex_rgb(hex)
        local chroma = math.max(r, g, b) - math.min(r, g, b)
        local contrast = math.abs(codex_brightness(r, g, b) - background_brightness)
        local accent_distance = math.sqrt((r - accent_r)^2 + (g - accent_g)^2 + (b - accent_b)^2)
        local hue_weight = 1
        local hue = codex_hue(r, g, b)
        if hue and accent_hue then
            local distance = math.abs(hue - accent_hue)
            local separation = 2 * math.min(distance, 1 - distance)
            hue_weight = 0.05 + separation^2
        end
        -- Favor a distinct hue, not just a brighter shade of the normal border.
        local score = (0.1 + chroma) * contrast * math.min(1, accent_distance / 0.4) * hue_weight
        if score > codex_best_score then
            codex_working_hex, codex_best_score = hex, score
        end
    end
end
codex_working_hex = codex_working_hex or codex_palette.accent or codex_palette.foreground

-- The spinner disappears when Codex stops; dynamic rules restore the theme border.
local codex_working_rules = { tag = "+codex-working-" .. (codex_working_hex or "theme") }
if codex_working_hex then
    local color = "rgb(" .. codex_working_hex .. ")"
    codex_working_rules.border_color = color .. " " .. color
end
o.window({ tag = "terminal", title = "^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] .*" }, codex_working_rules)
