#!/usr/bin/lua

-- be3600-player.lua -- plays a .bea animation on the BE3600's front display
-- by writing raw frames straight to /dev/fb0, looping forever.
-- Usage: be3600-player.lua [file.bea]   (default /etc/be3600-screen/active.bea)
--
-- The display is 76 x 284 pixels at 16 bits per pixel with a 152-byte
-- row stride, so one frame is 152 * 284 = 43168 bytes.
-- File format: see docs/BEA-FORMAT.md.

local PACK =
    arg[1] or
    "/etc/be3600-screen/active.bea"

local FRAME_BYTES = 43168

-- Test hooks (unused in normal operation):
--   BE3600_FB     framebuffer path; point it at an ordinary file to test on a PC
--   BE3600_LOOPS  exit after this many complete passes instead of looping forever
local FB_PATH =
    os.getenv("BE3600_FB") or
    "/dev/fb0"

local MAX_LOOPS =
    tonumber(os.getenv("BE3600_LOOPS") or "")

local loops = 0


local function u16(s,p)

    local a,b =
        s:byte(p,p+1)

    assert(a and b)

    return a + b*256
end


local function u32(s,p)

    local a,b,c,d =
        s:byte(p,p+3)

    assert(a and d)

    return
        a +
        b*256 +
        c*65536 +
        d*16777216
end


local f =
    assert(
        io.open(
            PACK,
            "rb"
        )
    )


local header =
    assert(
        f:read(12)
    )


assert(
    header:sub(1,4) == "BEA1",
    "Invalid BEA animation"
)


local fps =
    u16(header,5)

local records =
    u16(header,7)

local bytes =
    u32(header,9)


assert(
    bytes == FRAME_BYTES,
    "Wrong framebuffer size"
)


assert(
    fps >= 1 and fps <= 24,
    "Invalid FPS"
)


-- microseconds per tick
local delay =
    math.floor(
        1000000 / fps
    )


while true do

    f:seek(
        "set",
        12
    )


    for i=1,records do

        -- how many ticks this frame stays on screen
        local rawRun =
            assert(
                f:read(2)
            )

        local run =
            u16(
                rawRun,
                1
            )


        local frame =
            assert(
                f:read(bytes)
            )


        --
        -- Open/write/close gives us a guaranteed
        -- framebuffer offset of zero every frame.
        --

        local fb =
            assert(
                io.open(
                    FB_PATH,
                    "wb"
                )
            )


        assert(
            fb:write(frame)
        )

        fb:flush()
        fb:close()


        os.execute(
            "/bin/usleep " ..
            tostring(
                delay * run
            )
        )
    end

    loops = loops + 1

    if MAX_LOOPS and loops >= MAX_LOOPS then
        os.exit(0)
    end
end
