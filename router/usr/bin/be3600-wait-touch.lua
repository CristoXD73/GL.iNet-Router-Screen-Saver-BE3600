#!/usr/bin/lua

-- be3600-wait-touch.lua -- blocks until a finger touches the screen.
-- Exit codes: 0 = finger down, 2 = the touch device could not be read.
--
-- The touch controller is a Hynitron CST816X on /dev/input/event0. It reports
-- ABS_X / ABS_Y coordinates, ABS_MT_TRACKING_ID (0 while a finger is down,
-- -1 when it lifts) and SYN_REPORT. It does not send BTN_TOUCH, but that and
-- BTN_TOOL_FINGER are accepted too in case a firmware update changes that.

local DEVICE = "/dev/input/event0"

local function u16(s,p)
    local a,b = s:byte(p,p+1)

    if not a or not b then
        return nil
    end

    return a + b*256
end


local function i32(s,p)
    local a,b,c,d =
        s:byte(p,p+3)

    if not a or not d then
        return nil
    end

    local n =
        a +
        b*256 +
        c*65536 +
        d*16777216

    if n >= 2147483648 then
        n = n - 4294967296
    end

    return n
end


local f =
    assert(
        io.open(
            DEVICE,
            "rb"
        )
    )

-- IMPORTANT: unbuffered reads.
--
-- Observed on the GL-BE3600: with Lua's default buffered stream,
-- f:read(24) on this device returned nil immediately, with no touch,
-- so this script exited 2 on every call. With buffering off it blocks
-- until a real event arrives and detects touches correctly. (The raw
-- device itself was always fine: `dd` on it blocked and delivered
-- events.)
--
-- The likely cause: input devices reject reads smaller than one whole
-- struct input_event (24 bytes here), and a buffered stdio read can be
-- issued slightly smaller than the 24 bytes asked for. That mechanism
-- was not confirmed; the fix below is what was verified.
f:setvbuf("no")


while true do

    local e = f:read(24)

    if not e or #e ~= 24 then
        os.exit(2)
    end


    local typ =
        u16(e,17)

    local code =
        u16(e,19)

    local value =
        i32(e,21)


    -- EV_KEY / BTN_TOUCH / press
    if typ == 1 and
       code == 330 and
       value == 1 then

        os.exit(0)
    end


    -- EV_KEY / BTN_TOOL_FINGER / press
    if typ == 1 and
       code == 325 and
       value == 1 then

        os.exit(0)
    end


    -- EV_ABS / ABS_MT_TRACKING_ID
    --
    -- >= 0 begins a finger contact.
    -- -1 means release.
    if typ == 3 and
       code == 57 and
       value >= 0 then

        os.exit(0)
    end
end
