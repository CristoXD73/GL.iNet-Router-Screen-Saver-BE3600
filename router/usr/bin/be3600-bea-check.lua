#!/usr/bin/lua

-- be3600-bea-check.lua -- validate a .bea animation and report its length.
-- Usage: be3600-bea-check.lua [file.bea]
-- Exit 0 and print a summary if valid; exit 1 with a reason if not.

local PATH = arg[1] or "/etc/be3600-screen/active.bea"
local FRAME_BYTES = 43168

local function fail(msg)
    io.stderr:write("INVALID: " .. msg .. "\n")
    os.exit(1)
end

local function u16(s, p)
    local a, b = s:byte(p, p + 1)
    return a + b * 256
end

local function u32(s, p)
    local a, b, c, d = s:byte(p, p + 3)
    return a + b * 256 + c * 65536 + d * 16777216
end

local f = io.open(PATH, "rb")
if not f then fail("cannot open " .. PATH) end

local header = f:read(12)
if not header or #header ~= 12 then fail("file shorter than the 12-byte header") end
if header:sub(1, 4) ~= "BEA1" then fail("bad magic (expected BEA1)") end

local fps     = u16(header, 5)
local records = u16(header, 7)
local bytes   = u32(header, 9)

if bytes ~= FRAME_BYTES then
    fail("frame size is " .. bytes .. " bytes, this display needs " .. FRAME_BYTES)
end
if fps < 1 or fps > 24 then fail("fps " .. fps .. " outside 1..24") end
if records < 1 then fail("no frames") end

local size = f:seek("end")
local expected = 12 + records * (2 + bytes)
if size ~= expected then
    fail("size is " .. size .. " bytes but the header implies " .. expected)
end

local ticks = 0
for i = 0, records - 1 do
    f:seek("set", 12 + i * (2 + bytes))
    local raw = f:read(2)
    if not raw or #raw ~= 2 then fail("truncated at record " .. (i + 1)) end
    ticks = ticks + u16(raw, 1)
end
f:close()

print(string.format(
    "OK %s: %d frames, %d ticks at %d fps = %.1f s per loop, %d bytes",
    PATH, records, ticks, fps, ticks / fps, size))
