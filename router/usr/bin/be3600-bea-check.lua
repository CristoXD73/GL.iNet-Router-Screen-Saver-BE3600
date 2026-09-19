#!/usr/bin/lua

-- be3600-bea-check.lua -- validate a .bea animation (BEA1 or BEA2) and report its length.
-- Usage: be3600-bea-check.lua [file.bea]
-- Exit 0 and print a summary if valid; exit 1 with a reason if not.
-- Format: see docs/BEA-FORMAT.md

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

local magic = header:sub(1, 4)
if magic ~= "BEA1" and magic ~= "BEA2" then fail("bad magic (expected BEA1 or BEA2)") end

local fps     = u16(header, 5)
local records = u16(header, 7)
local bytes   = u32(header, 9)

if bytes ~= FRAME_BYTES then
    fail("frame size is " .. bytes .. " bytes, this display needs " .. FRAME_BYTES)
end
if fps < 1 or fps > 24 then fail("fps " .. fps .. " outside 1..24") end
if records < 1 then fail("no frames") end

local size = f:seek("end")
local ticks = 0

if magic == "BEA1" then

    local expected = 12 + records * (2 + bytes)
    if size ~= expected then
        fail("size is " .. size .. " bytes but the header implies " .. expected)
    end

    for i = 0, records - 1 do
        f:seek("set", 12 + i * (2 + bytes))
        local raw = f:read(2)
        if not raw or #raw ~= 2 then fail("truncated at record " .. (i + 1)) end
        ticks = ticks + u16(raw, 1)
    end

else

    -- BEA2: each record is  u16 run, u8 kind, u32 payload_length, payload.
    --   kind 0 FULL   payload = one whole frame
    --   kind 1 DELTA  payload = u16 nspans, then nspans x (u32 offset, u16 length, data)
    --   kind 2 HOLD   payload empty: the previous picture stays
    -- The first record must be FULL (the loop restarts from it), and every span
    -- must lie inside the frame. Everything is checked here so the player never
    -- has to trust the file.
    local pos = 12
    local nfull, ndelta, nhold = 0, 0, 0

    for i = 1, records do

        f:seek("set", pos)
        local rec = f:read(7)
        if not rec or #rec ~= 7 then fail("truncated at record " .. i) end

        local run  = u16(rec, 1)
        local kind = rec:byte(3)
        local plen = u32(rec, 4)

        if pos + 7 + plen > size then fail("record " .. i .. " runs past the end of the file") end
        if i == 1 and kind ~= 0 then fail("the first record must be a full frame") end

        if kind == 0 then
            if plen ~= bytes then fail("record " .. i .. ": a full frame must be " .. bytes .. " bytes") end
            nfull = nfull + 1

        elseif kind == 1 then
            local payload = f:read(plen)
            if not payload or #payload ~= plen or plen < 2 then fail("record " .. i .. ": bad delta") end

            local n = u16(payload, 1)
            local p = 3
            for s = 1, n do
                if p + 5 > plen then fail("record " .. i .. ", span " .. s .. ": truncated") end
                local off = u32(payload, p)
                local len = u16(payload, p + 4)
                if len < 1 or off + len > bytes then
                    fail("record " .. i .. ", span " .. s .. ": outside the frame")
                end
                p = p + 6 + len
            end
            if p - 1 ~= plen then fail("record " .. i .. ": delta length does not match its spans") end
            ndelta = ndelta + 1

        elseif kind == 2 then
            if plen ~= 0 then fail("record " .. i .. ": a hold record has no data") end
            nhold = nhold + 1

        else
            fail("record " .. i .. ": unknown kind " .. kind)
        end

        ticks = ticks + run
        pos = pos + 7 + plen
    end

    if pos ~= size then fail((size - pos) .. " unexpected bytes after the last record") end
end

f:close()

print(string.format(
    "OK %s: %d frames, %d ticks at %d fps = %.1f s per loop, %d bytes",
    PATH, records, ticks, fps, ticks / fps, size))
