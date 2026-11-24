"""Run generated Roblox Lua under the system's Lua 5.4 (via ctypes) with a tiny mock of the
Roblox API. It cannot prove the script behaves correctly inside Studio - it proves the script
is valid Lua, runs to completion, finds/places parts, builds the expected constraints and
that the driver script reacts to throttle/steer with the intended signs."""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import List, Optional, Tuple


def _load():
    for name in ("liblua5.4.so.0", "liblua5.4.so", ctypes.util.find_library("lua5.4") or "", "liblua.so"):
        if not name:
            continue
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


LUA = _load()
AVAILABLE = LUA is not None

MOCK = r'''
LOG = {}
SEL = {}
HEARTBEAT = {}
function print(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end
  LOG[#LOG + 1] = table.concat(t, " ")
end
function warn(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end
  LOG[#LOG + 1] = "WARN: " .. table.concat(t, " ")
end

-- Vector3 / CFrame --------------------------------------------------------------
local V3 = {}
V3.__index = V3
Vector3 = { new = function(x, y, z) return setmetatable({ X = x or 0, Y = y or 0, Z = z or 0 }, V3) end }

local function matmul(a, b)
  local r = {}
  for i = 1, 3 do
    r[i] = {}
    for j = 1, 3 do
      r[i][j] = a[i][1] * b[1][j] + a[i][2] * b[2][j] + a[i][3] * b[3][j]
    end
  end
  return r
end
local CF = {}
CF.__index = CF
local I3 = { { 1, 0, 0 }, { 0, 1, 0 }, { 0, 0, 1 } }
local function mk(p, r) return setmetatable({ Position = p, R = r }, CF) end
CFrame = {}
function CFrame.new(x, y, z)
  if type(x) == "table" then return mk(Vector3.new(x.X, x.Y, x.Z), I3) end
  return mk(Vector3.new(x, y, z), I3)
end
function CFrame.Angles(rx, ry, rz)
  local cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
  local Rx = { { 1, 0, 0 }, { 0, cx, -sx }, { 0, sx, cx } }
  local Ry = { { cy, 0, sy }, { 0, 1, 0 }, { -sy, 0, cy } }
  local Rz = { { cz, -sz, 0 }, { sz, cz, 0 }, { 0, 0, 1 } }
  return mk(Vector3.new(0, 0, 0), matmul(matmul(Rx, Ry), Rz))
end
CF.__mul = function(a, b)
  if getmetatable(b) == CF then
    local p = a.Position
    local q = b.Position
    local R = a.R
    return mk(Vector3.new(p.X + R[1][1] * q.X + R[1][2] * q.Y + R[1][3] * q.Z,
                          p.Y + R[2][1] * q.X + R[2][2] * q.Y + R[2][3] * q.Z,
                          p.Z + R[3][1] * q.X + R[3][2] * q.Y + R[3][3] * q.Z), matmul(a.R, b.R))
  end
  error("unsupported CFrame multiplication")
end

PhysicalProperties = { new = function(...) return { ... } end }
Enum = setmetatable({}, { __index = function(t, k)
  local e = setmetatable({}, { __index = function(_, v) return k .. "." .. v end })
  rawset(t, k, e)
  return e
end })

-- Instances ---------------------------------------------------------------------
local derived = {
  Part = { BasePart = true }, MeshPart = { BasePart = true }, VehicleSeat = { BasePart = true },
  HingeConstraint = { Constraint = true }, WeldConstraint = { Constraint = true },
}
local M = {}
function M.IsA(self, cls)
  return self.ClassName == cls or (derived[self.ClassName] ~= nil and derived[self.ClassName][cls] == true)
end
function M.GetChildren(self) local t = {} for _, c in ipairs(self._children) do t[#t + 1] = c end return t end
function M.GetDescendants(self)
  local out = {}
  local function rec(n) for _, c in ipairs(n._children) do out[#out + 1] = c rec(c) end end
  rec(self)
  return out
end
function M.SetAttribute(self, k, v) self._attrs[k] = v end
function M.GetAttribute(self, k) return self._attrs[k] end
function M.FindFirstChild(self, name) for _, c in ipairs(self._children) do if c.Name == name then return c end end return nil end
function M.WaitForChild(self, name) return M.FindFirstChild(self, name) end
function M.FindFirstChildOfClass(self, cls) for _, c in ipairs(self._children) do if M.IsA(c, cls) then return c end end return nil end
local function detach(o)
  local p = rawget(o, "_parent")
  if p then for i, c in ipairs(p._children) do if c == o then table.remove(p._children, i) break end end end
  rawset(o, "_parent", nil)
end
function M.Destroy(self) detach(self) end

local meta = {
  __index = function(t, k)
    if k == "Parent" then return rawget(t, "_parent") end
    return M[k]
  end,
  __newindex = function(t, k, v)
    if k == "Parent" then
      detach(t)
      if v ~= nil then rawset(t, "_parent", v) v._children[#v._children + 1] = t end
    else
      rawset(t, k, v)
    end
  end,
}
Instance = { new = function(cls)
  local o = { ClassName = cls, Name = cls, _children = {}, _attrs = {}, Anchored = false, CanCollide = true,
              Massless = false, Transparency = 0, AssemblyMass = 100, ThrottleFloat = 0, SteerFloat = 0 }
  o.CFrame = CFrame.new(0, 0, 0)
  if cls == "ProximityPrompt" then o.Triggered = { Connect = function(self, fn) o._triggered = fn end } end
  return setmetatable(o, meta)
end }

workspace = Instance.new("Workspace")
local selection = {
  Get = function(self) return SEL end,
  Set = function(self, t) SEL = t end,
}
local runservice = { Heartbeat = { Connect = function(self, fn) HEARTBEAT[#HEARTBEAT + 1] = fn end } }
game = { GetService = function(self, name)
  if name == "Selection" then return selection end
  if name == "RunService" then return runservice end
  error("service not mocked: " .. tostring(name))
end }
'''


class LuaState:
    def __init__(self) -> None:
        if LUA is None:
            raise RuntimeError("liblua5.4 not available")
        L = LUA
        L.luaL_newstate.restype = ctypes.c_void_p
        L.luaL_openlibs.argtypes = [ctypes.c_void_p]
        L.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.luaL_loadstring.restype = ctypes.c_int
        L.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_ssize_t, ctypes.c_void_p]
        L.lua_pcallk.restype = ctypes.c_int
        L.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.lua_tolstring.restype = ctypes.c_char_p
        L.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.lua_close.argtypes = [ctypes.c_void_p]
        self.L = L.luaL_newstate()
        L.luaL_openlibs(self.L)

    def run(self, code: str) -> Tuple[bool, str]:
        """Execute a chunk; returns (ok, returned string or error message)."""
        L, st = LUA, self.L
        status = L.luaL_loadstring(st, code.encode("utf-8"))
        if status == 0:
            status = L.lua_pcallk(st, 0, 1, 0, 0, None)
        msg = L.lua_tolstring(st, -1, None)
        text = msg.decode("utf-8", "replace") if msg is not None else ""
        L.lua_settop(st, 0)
        return status == 0, text

    def close(self) -> None:
        LUA.lua_close(self.L)


def run_rig(script: str, piece_names: List[str], *, select: bool = True,
            rename: Optional[dict] = None, drop: Tuple[str, ...] = (),
            sizes: Optional[dict] = None) -> Tuple[bool, str]:
    """Build a fake imported Model holding MeshParts, run the generated script on it, then exercise
    the driver Script. Returns (ok, text) where text is the log plus RESULT lines."""
    rename = rename or {}
    sizes = sizes or {}

    def size_stmt(n: str) -> str:
        if n not in sizes:
            return ""
        x, y, z = sizes[n]
        return f"p.Size = Vector3.new({x}, {y}, {z}) "

    mk = "\n".join(
        f'do local p = Instance.new("MeshPart") p.Name = "{rename.get(n, n)}" {size_stmt(n)}p.Parent = SRC end'
        for n in piece_names if n not in drop)
    setup = f'''
SRC = Instance.new("Model")
SRC.Name = "Imported"
SRC.Parent = workspace
{mk}
SEL = {{ SRC }}
''' if select else "SEL = {}\n"
    suffix = r'''
local R = {}
local function add(k, v) LOG[#LOG + 1] = "RESULT " .. k .. "=" .. tostring(v) end
local welds, hd, hs, hf, seats, scripts, anchored, parts = 0, 0, 0, 0, 0, 0, 0, 0
local seatObj, driver
local steerAtt, xAxisOfSteer
for _, d in ipairs(car:GetDescendants()) do
  if d:IsA("WeldConstraint") then welds = welds + 1 end
  if d:IsA("HingeConstraint") then
    local role = d:GetAttribute("CarRole")
    if role == "Drive" then hd = hd + 1 elseif role == "Steer" then hs = hs + 1 steerAtt = d.Attachment0 else hf = hf + 1 end
  end
  if d.ClassName == "VehicleSeat" then seats = seats + 1 if d.Name == "VehicleSeat" then seatObj = d end end
  if d.ClassName == "Script" then scripts = scripts + 1 driver = d end
  if d:IsA("BasePart") then
    parts = parts + 1
    if d.Anchored then anchored = anchored + 1 end
    add("pos_" .. d.Name, string.format("%.3f,%.3f,%.3f", d.CFrame.Position.X, d.CFrame.Position.Y, d.CFrame.Position.Z))
  end
end
local nocol, colliders, uprights, suspensions, springs, knuckles = 0, 0, 0, 0, 0, 0
for _, d in ipairs(car:GetDescendants()) do
  if d.ClassName == "NoCollisionConstraint" then nocol = nocol + 1 end
  if d:IsA("BasePart") and string.sub(d.Name, 1, 14) == "WheelCollider_" then colliders = colliders + 1 end
  if d:IsA("BasePart") and string.sub(d.Name, 1, 8) == "Upright_" then uprights = uprights + 1 end
  if d:IsA("BasePart") and string.sub(d.Name, 1, 8) == "Knuckle_" then knuckles = knuckles + 1 end
  if d.ClassName == "PrismaticConstraint" then suspensions = suspensions + 1 end
  if d.ClassName == "SpringConstraint" then springs = springs + 1 end
end
add("nocollide", nocol) add("colliders", colliders)
add("uprights", uprights) add("suspensions", suspensions) add("springs", springs) add("knuckles", knuckles)
add("advanced_chassis", car:GetAttribute("AdvancedChassis"))
add("prompt", seatObj ~= nil and seatObj:FindFirstChild("DrivePrompt") ~= nil)
add("seat_collides", seatObj ~= nil and seatObj.CanCollide)
add("maxspeed", car:GetAttribute("MaxSpeed"))
add("welds", welds) add("drive", hd) add("steer", hs) add("free", hf) add("seats", seats)
add("scripts", scripts) add("anchored", anchored) add("parts", parts)
add("primary", car.PrimaryPart ~= nil and car.PrimaryPart.Name or "none")
add("selected", SEL[1] == car)
add("source_left", SRC.Parent ~= nil)
if steerAtt then
  -- the steering hinge axis (attachment X) must point along world Y
  local R = steerAtt.WorldCFrame.R
  add("steer_axis", string.format("%.2f,%.2f,%.2f", R[1][1], R[2][1], R[3][1]))
end
if driver then
  local env = setmetatable({ script = driver }, { __index = _G })
  local fn, err = load(driver.Source, "driver", "t", env)
  if not fn then error("driver does not compile: " .. tostring(err)) end
  fn()
  seatObj.ThrottleFloat = 1
  seatObj.SteerFloat = 1
  for _, f in ipairs(HEARTBEAT) do f() end
  for _, d in ipairs(car:GetDescendants()) do
    if d:IsA("HingeConstraint") and d:GetAttribute("CarRole") == "Drive" then
      add("drive_w_" .. d.Name, d.AngularVelocity)
      add("drive_torque_" .. d.Name, d.MotorMaxTorque)
    elseif d:IsA("HingeConstraint") and d:GetAttribute("CarRole") == "Steer" then
      add("steer_angle_" .. d.Name, d.TargetAngle)
    end
  end
  add("engine_rpm", car:GetAttribute("EngineRPM"))
  add("gear", car:GetAttribute("Gear"))
end
return table.concat(LOG, "\n")
'''
    code = MOCK + "\n" + setup + "\n" + script + "\n" + suffix
    st = LuaState()
    try:
        return st.run(code)
    finally:
        st.close()


def run_achassis_rig(script: str, piece_names: List[str], *, select: bool = True) -> Tuple[bool, str]:
    """Like run_rig, but inspects the real-A-Chassis object tree (DriveSeat/Wheels/Body/"A-Chassis
    Tune") instead of car2roblox's own simple rig. It never executes the embedded Initialize.luau
    or Drive.luau (that would need a much bigger Players/RunService mock) - it only checks the
    shape Initialize.luau's own code requires, and that every embedded script's .Source round-trips
    back out byte-for-byte (the thing most likely to break from the Lua-string-escaping step)."""
    mk = "\n".join(
        f'do local p = Instance.new("MeshPart") p.Name = "{n}" p.Parent = SRC end' for n in piece_names)
    setup = f'''
SRC = Instance.new("Model")
SRC.Name = "Imported"
SRC.Parent = workspace
{mk}
SEL = {{ SRC }}
''' if select else "SEL = {}\n"
    suffix = r'''
local function add(k, v) LOG[#LOG + 1] = "RESULT " .. k .. "=" .. tostring(v) end
local ds = car:FindFirstChild("DriveSeat")
add("driveseat", ds ~= nil and ds.ClassName)
add("driveseat_cancollide", ds ~= nil and ds.CanCollide)
add("primary_is_driveseat", car.PrimaryPart == ds)

local wheelsFolder = car:FindFirstChild("Wheels")
add("wheels_folder", wheelsFolder ~= nil and wheelsFolder.ClassName)
local wheelCount = 0
for _, slot in ipairs({ "FL", "FR", "RL", "RR" }) do
  local w = wheelsFolder and wheelsFolder:FindFirstChild(slot)
  if w then
    wheelCount = wheelCount + 1
    local parts = w:FindFirstChild("Parts")
    add("wheel_" .. slot .. "_parts_count", parts and #parts:GetChildren() or -1)
    local wf = w:FindFirstChild("WheelFixed")
    add("wheel_" .. slot .. "_wheelfixed", wf ~= nil and wf.ClassName)
  end
end
add("wheel_count", wheelCount)

local body = car:FindFirstChild("Body")
add("body_folder", body ~= nil and body.ClassName)
add("body_children_count", body and #body:GetChildren())

local tune = car:FindFirstChild("A-Chassis Tune")
add("tune_class", tune ~= nil and tune.ClassName)
add("tune_source_roundtrip", tune ~= nil and (tune.Source == DATA.achassis.tune))

local initS = tune and tune:FindFirstChild("Initialize")
add("init_roundtrip", initS ~= nil and (initS.Source == DATA.achassis.initialize))

local readme = tune and tune:FindFirstChild("README")
add("readme_roundtrip", readme ~= nil and (readme.Source == DATA.achassis.readme))
local units = readme and readme:FindFirstChild("Units")
add("units_roundtrip", units ~= nil and (units.Source == DATA.achassis.units))

local plugins = tune and tune:FindFirstChild("Plugins")
add("plugins_empty", plugins ~= nil and #plugins:GetChildren() == 0)

local iface = tune and tune:FindFirstChild("A-Chassis Interface")
add("iface_class", iface ~= nil and iface.ClassName)
local values = iface and iface:FindFirstChild("Values")
add("values_count", values ~= nil and #values:GetChildren())
local mobile = iface and iface:FindFirstChild("Mobile")
add("mobile_class", mobile ~= nil and mobile.ClassName)
local isOn = iface and iface:FindFirstChild("IsOn")
local starting = isOn and isOn:FindFirstChild("Starting")
add("starting_class", starting ~= nil and starting.ClassName)
add("controlsopen_class", iface and iface:FindFirstChild("ControlsOpen") and
    iface:FindFirstChild("ControlsOpen").ClassName)
local drive = iface and iface:FindFirstChild("Drive")
add("drive_roundtrip", drive ~= nil and (drive.Source == DATA.achassis.drive))
add("selected", SEL[1] == car)
return table.concat(LOG, "\n")
'''
    code = MOCK + "\n" + setup + "\n" + script + "\n" + suffix
    st = LuaState()
    try:
        return st.run(code)
    finally:
        st.close()


def parse_results(text: str) -> dict:
    out = {}
    for ln in text.splitlines():
        if ln.startswith("RESULT "):
            k, _, v = ln[7:].partition("=")
            out[k] = v
    return out
