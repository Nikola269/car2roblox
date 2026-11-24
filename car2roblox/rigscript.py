"""Generates the Roblox Studio setup script that turns the imported meshes into a drivable car.

The script is plain Lua (no Luau-only syntax), run from Studio's Command Bar after importing
the model. It places every MeshPart from the converter's manifest, welds the body together,
builds wheel hinges / steering servos, adds a VehicleSeat and a small driving Script.
"""
from __future__ import annotations

from . import __version__
from .export import to_lua

_DRIVER = r'''
local RunService = game:GetService("RunService")
local car = script.Parent
local seat = car:WaitForChild("VehicleSeat")

-- every seat (the driver's and any passenger seats) gets in the same way: walk up, press E.
-- the seats are CanCollide = false (so the doors pass through), which also means Roblox's normal
-- touch-to-sit never fires, so this does it by hand.
for _, c in ipairs(car:GetDescendants()) do
	if c:IsA("VehicleSeat") then
		local seatPrompt = c:FindFirstChildOfClass("ProximityPrompt")
		if seatPrompt ~= nil then
			seatPrompt.Triggered:Connect(function(player)
				local character = player.Character
				local humanoid = character and character:FindFirstChildOfClass("Humanoid")
				if humanoid ~= nil and c.Occupant == nil then
					c:Sit(humanoid)
				end
			end)
		end
	end
end

local drive, steer = {}, {}
for _, c in ipairs(car:GetDescendants()) do
	if c:IsA("HingeConstraint") then
		local role = c:GetAttribute("CarRole")
		if role == "Drive" then
			table.insert(drive, c)
		elseif role == "Steer" then
			table.insert(steer, c)
		end
	end
end

-- The A-Chassis style drivetrain: a small simulated engine with a torque curve and a gearbox,
-- instead of just mapping the throttle straight onto wheel speed. It uses the trick real Roblox
-- vehicle rigs use: the motor's target AngularVelocity is set far beyond anything the car can
-- reach, so the wheel's *actual* speed is decided entirely by how much MotorMaxTorque the
-- "engine" can deliver at the current RPM - the same way a real drivetrain behaves (it labours
-- in a low gear up a hill rather than snapping straight to a fixed speed).
local GEAR_RATIOS = { 3.60, 2.19, 1.41, 1.00, 0.78 }
local FINAL_DRIVE = 3.9
local IDLE_RPM, REDLINE_RPM = 900, 6500
local SHIFT_UP_RPM, SHIFT_DOWN_RPM = 6000, 2400
-- torque as a fraction of peak, sampled across the rev range (idle -> redline)
local TORQUE_CURVE = {
	{ 0.00, 0.45 }, { 0.15, 0.75 }, { 0.35, 1.00 }, { 0.55, 0.95 }, { 0.75, 0.80 }, { 1.00, 0.55 },
}
local gear = 1
local engineRPM = IDLE_RPM

local function clamp(x, lo, hi)
	if x < lo then return lo elseif x > hi then return hi else return x end
end

local function torqueFactor(rpm)
	local frac = clamp((rpm - IDLE_RPM) / (REDLINE_RPM - IDLE_RPM), 0, 1)
	for i = 1, #TORQUE_CURVE - 1 do
		local a, b = TORQUE_CURVE[i], TORQUE_CURVE[i + 1]
		if frac <= b[1] then
			local t = (frac - a[1]) / math.max(b[1] - a[1], 0.0001)
			return a[2] + (b[2] - a[2]) * t
		end
	end
	return TORQUE_CURVE[#TORQUE_CURVE][2]
end

-- the real spin speed of the drive wheels (rad/s about the world-X axle, see rigscript.py) -
-- used to work out what RPM the engine would be turning at in the current gear
local function driveWheelSpeed()
	local total, n = 0, 0
	for _, h in ipairs(drive) do
		local part = h.Attachment1 and h.Attachment1.Parent
		local av = part and part.AssemblyAngularVelocity
		if av ~= nil then
			total = total + math.abs(av.X)
			n = n + 1
		end
	end
	if n == 0 then
		return 0
	end
	return total / n
end

RunService.Heartbeat:Connect(function(dt)
	-- read the tuning attributes every frame so they can be edited live on the Model
	local driveSign = car:GetAttribute("DriveSign") or -1
	local steerSign = car:GetAttribute("SteerSign") or -1
	local maxSpeed = car:GetAttribute("MaxSpeed") or 90
	local maxSteer = car:GetAttribute("MaxSteerDegrees") or 30
	local advanced = car:GetAttribute("AdvancedChassis") == true
	local throttle = seat.ThrottleFloat
	local steerInput = seat.SteerFloat

	if advanced then
		dt = dt or (1 / 60)
		local ratio = GEAR_RATIOS[gear] * FINAL_DRIVE
		local wheelRPM = driveWheelSpeed() * 60 / (2 * math.pi) * ratio
		local targetRPM = math.max(IDLE_RPM, wheelRPM)
		-- smooth the reading into something that behaves like a real tachometer needle instead
		-- of snapping to a new value every frame
		engineRPM = engineRPM + (targetRPM - engineRPM) * clamp(dt * 6, 0, 1)
		if math.abs(throttle) < 0.02 then
			engineRPM = engineRPM + (IDLE_RPM - engineRPM) * clamp(dt * 2, 0, 1)
		end
		engineRPM = clamp(engineRPM, IDLE_RPM * 0.9, REDLINE_RPM * 1.05)

		if math.abs(throttle) > 0.05 then
			if engineRPM >= SHIFT_UP_RPM and gear < #GEAR_RATIOS then
				gear = gear + 1
			elseif engineRPM <= SHIFT_DOWN_RPM and gear > 1 then
				gear = gear - 1
			end
		end

		local peakTorque = car:GetAttribute("PeakTorque") or 2000
		local wheelTorque = peakTorque * torqueFactor(engineRPM) * ratio * math.abs(throttle)
		local dir = driveSign * ((throttle < 0) and -1 or 1)
		for _, h in ipairs(drive) do
			-- an unreachable target speed in the right direction: the wheel is really driven by
			-- MotorMaxTorque below, not by this number
			h.AngularVelocity = dir * 9999
			h.MotorMaxTorque = wheelTorque / math.max(1, #drive)
		end
		car:SetAttribute("EngineRPM", math.floor(engineRPM + 0.5))
		car:SetAttribute("Gear", gear)
	else
		for _, h in ipairs(drive) do
			local r = h:GetAttribute("Radius") or 1
			h.AngularVelocity = driveSign * throttle * maxSpeed / r
		end
	end

	for _, s in ipairs(steer) do
		s.TargetAngle = steerSign * steerInput * maxSteer
	end
end)
'''

_TEMPLATE = r'''--[[
	car2roblox __VERSION__  -  Studio setup script for "__NAME__"

	HOW TO USE
	  1. Import __GLB__ with File > Import (Scale Unit = Studs, "Import Only As Model" ticked).
	  2. Select the imported Model in the Explorer.
	  3. Paste this whole script into the Command Bar (View > Command Bar) and press Enter.

	The script finds each imported mesh by name, puts it at the position stored below, welds the
	body together, builds wheels (hinge motors, front wheels steer through a servo), a driver
	seat and a driving Script. It is a simple starter rig: tune the numbers in TUNING (or the
	attributes on the finished Model) to taste.
]]

local DATA = __DATA__

local TUNING = {
	DriveSign = -1,          -- set to 1 if the car drives backwards
	SteerSign = -1,          -- set to 1 if the steering is inverted
	MaxSpeed = DATA.top_speed or 90,   -- studs per second (1 stud = 0.28 m, so 90 is about 91 km/h)
	MaxSteerDegrees = 30,
	Acceleration = 35,       -- studs/s^2 used to size the motor torque
	SpawnHeight = 2,         -- the car is dropped from this height (studs) above where it was exported
	SuspensionTravel = 0.6,  -- studs of wheel travel each way, only used by the A-Chassis-style rig
}

local Selection = game:GetService("Selection")
local source = Selection:Get()[1]
if source == nil then
	error("car2roblox: select the imported car (the Model or Folder the 3D Importer created) first, then run this script again.")
end

----------------------------------------------------------------------------------------------
-- find the imported MeshParts by name
local function key(s)
	return string.lower((string.gsub(s, "[^%w]", "")))
end

local pool = {}
local function collect(inst)
	if inst:IsA("BasePart") then
		table.insert(pool, inst)
	end
	for _, d in ipairs(inst:GetDescendants()) do
		if d:IsA("BasePart") then
			table.insert(pool, d)
		end
	end
end
collect(source)

local claimed = {}

-- pass 1: the name matches exactly
local function matchExact(spec)
	local k = key(spec.name)
	for _, d in ipairs(pool) do
		if not claimed[d] and key(d.Name) == k then
			return d
		end
	end
	return nil
end

-- pass 2: the importer added a suffix (Name_1, Name.001, Name_Node, Name_Mesh)
local function matchSuffix(spec)
	local k = key(spec.name)
	for _, d in ipairs(pool) do
		if not claimed[d] then
			local dk = key(d.Name)
			if string.sub(dk, 1, #k) == k then
				local rest = string.sub(dk, #k + 1)
				if string.match(rest, "^%d*$") or string.match(rest, "^node%d*$") or string.match(rest, "^mesh%d*$") then
					return d
				end
			end
		end
	end
	return nil
end

-- pass 3 (last resort): the name was changed completely, so compare bounding-box sizes
local function matchSize(spec)
	if spec.size == nil then
		return nil
	end
	local want = { spec.size[1], spec.size[2], spec.size[3] }
	table.sort(want)
	local best, bestErr = nil, nil
	for _, d in ipairs(pool) do
		if not claimed[d] and d.Size ~= nil then
			local have = { d.Size.X, d.Size.Y, d.Size.Z }
			table.sort(have)
			local err = 0
			for i = 1, 3 do
				err = math.max(err, math.abs(have[i] - want[i]) / math.max(want[i], 0.05))
			end
			if err < 0.04 and (bestErr == nil or err < bestErr) then
				best, bestErr = d, err
			end
		end
	end
	return best
end

local found = {}
local bySize = 0
for pass, matcher in ipairs({ matchExact, matchSuffix, matchSize }) do
	for _, spec in ipairs(DATA.pieces) do
		if found[spec.name] == nil then
			local d = matcher(spec)
			if d ~= nil then
				claimed[d] = true
				found[spec.name] = d
				if pass == 3 then
					bySize = bySize + 1
				end
			end
		end
	end
end
if bySize > 0 then
	print("car2roblox: " .. bySize .. " pieces were matched by size because the importer renamed them")
end

----------------------------------------------------------------------------------------------
local car = Instance.new("Model")
car.Name = DATA.name
car.Parent = workspace

local placed = {}
local missing = {}
for _, spec in ipairs(DATA.pieces) do
	local p = found[spec.name]
	if p == nil then
		table.insert(missing, spec.name)
	else
		p.Parent = car
		p.Anchored = true
		p.CanCollide = false
		p.Massless = true
		p.CFrame = CFrame.new(spec.center[1], spec.center[2] + TUNING.SpawnHeight, spec.center[3])
		if spec.kind == "glass" then
			p.Material = Enum.Material.Glass
			p.Transparency = 0.45
		end
		if spec.paint ~= nil then
			p:SetAttribute("CarPaint", spec.paint)
		end
		placed[spec.name] = p
	end
end
if #missing > 0 then
	warn("car2roblox: " .. #missing .. " pieces were not found among the imported parts: " .. table.concat(missing, ", "))
end
if next(placed) == nil then
	car:Destroy()
	error("car2roblox: none of the expected parts were found inside the selected object. Did you import the .glb from this folder?")
end

local function weld(a, b)
	local w = Instance.new("WeldConstraint")
	w.Part0 = a
	w.Part1 = b
	w.Parent = a
	return w
end

local function attach(parent, cf)
	local a = Instance.new("Attachment")
	a.Parent = parent
	a.WorldCFrame = cf
	return a
end

----------------------------------------------------------------------------------------------
local wheelPiece = {}
for _, w in ipairs(DATA.wheels) do
	for _, n in ipairs(w.pieces) do
		wheelPiece[n] = true
	end
end

if DATA.achassis_real then
	----------------------------------------------------------------------------------------------
	-- Real A-Chassis (lisphm, MPL-2.0, https://github.com/lisphm/A-Chassis): build the exact
	-- object tree its own Initialize.luau/Drive.luau expect, embed those scripts verbatim, and
	-- let them do all of the actual rig-building (suspension, motors, steering) at runtime -
	-- car2roblox only places the parts and the wiring Initialize.luau reads.
	local AC = DATA.achassis

	local driveSeat = Instance.new("VehicleSeat")
	driveSeat.Name = "DriveSeat"
	driveSeat.Size = Vector3.new(2, 1, 2)
	driveSeat.CFrame = CFrame.new(DATA.seat[1], DATA.seat[2] + TUNING.SpawnHeight, DATA.seat[3])
	driveSeat.Transparency = 1
	driveSeat.Anchored = true
	driveSeat.Massless = true
	driveSeat.CanCollide = true -- real A-Chassis is entered by touch, like any normal Roblox seat
	driveSeat.HeadsUpDisplay = false -- A-Chassis has its own on-screen dashboard
	driveSeat.Parent = car

	local wheelsFolder = Instance.new("Folder")
	wheelsFolder.Name = "Wheels"
	wheelsFolder.Parent = car

	local bodyFolder = Instance.new("Folder")
	bodyFolder.Name = "Body"
	bodyFolder.Parent = car

	local REAL_SLOTS = { FL = true, FR = true, RL = true, RR = true }
	for _, w in ipairs(DATA.wheels) do
		if REAL_SLOTS[w.slot] then
			-- the physical wheel A-Chassis spins: an invisible cylinder, same trick as the simple
			-- rig below. The real wheel/tyre meshes ride inside its "Parts" folder, which
			-- Initialize.luau welds to this part itself once the car starts.
			local wheelPart = Instance.new("Part")
			wheelPart.Name = w.slot
			wheelPart.Shape = Enum.PartType.Cylinder
			wheelPart.Size = Vector3.new(math.max(w.width * 0.9, 0.5), w.radius * 2, w.radius * 2)
			wheelPart.Transparency = 1
			wheelPart.Anchored = true
			wheelPart.CanCollide = true
			wheelPart.CustomPhysicalProperties = PhysicalProperties.new(0.7, 2.2, 0, 100, 1)
			wheelPart.CFrame = CFrame.new(w.hub[1], w.hub[2] + TUNING.SpawnHeight, w.hub[3])
			wheelPart.Parent = wheelsFolder

			local parts = Instance.new("Folder")
			parts.Name = "Parts"
			parts.Parent = wheelPart
			for _, n in ipairs(w.pieces) do
				local p = placed[n]
				if p ~= nil then
					p.Parent = parts
				end
			end

			-- Initialize.luau indexes this unconditionally even when nothing needs to go in it.
			local wheelFixed = Instance.new("Folder")
			wheelFixed.Name = "WheelFixed"
			wheelFixed.Parent = wheelPart
		else
			warn("car2roblox: wheel " .. w.slot .. " does not fit the real A-Chassis's front/rear "
				.. "left/right layout and was left out of the Wheels folder")
		end
	end

	for name, p in pairs(placed) do
		if not wheelPiece[name] then
			p.Parent = bodyFolder
		end
	end

	-- passenger seats (same convention as the simple rig: no controls, CanCollide off so the
	-- doors stay walk-through, welded in automatically along with the rest of car.Body)
	for i, pos in ipairs(DATA.passenger_seats or {}) do
		local pseat = Instance.new("VehicleSeat")
		pseat.Name = "PassengerSeat" .. i
		pseat.Size = Vector3.new(2, 1, 2)
		pseat.CFrame = CFrame.new(pos[1], pos[2] + TUNING.SpawnHeight, pos[3])
		pseat.Transparency = 1
		pseat.Anchored = true
		pseat.Massless = true
		pseat.CanCollide = false
		pseat.HeadsUpDisplay = false
		pseat.Parent = bodyFolder

		local pprompt = Instance.new("ProximityPrompt")
		pprompt.Name = "SitPrompt"
		pprompt.ActionText = "Sit"
		pprompt.ObjectText = DATA.name
		pprompt.HoldDuration = 0
		pprompt.MaxActivationDistance = 10
		pprompt.RequiresLineOfSight = false
		pprompt.Parent = pseat
	end

	-- "A-Chassis Tune": the module tree Initialize.luau/Drive.luau require() their way through.
	local tune = Instance.new("ModuleScript")
	tune.Name = "A-Chassis Tune"
	tune.Source = AC.tune
	tune.Parent = car

	local initScript = Instance.new("Script")
	initScript.Name = "Initialize"
	initScript.Source = AC.initialize
	initScript.Parent = tune

	local readmeModule = Instance.new("ModuleScript")
	readmeModule.Name = "README"
	readmeModule.Source = AC.readme
	readmeModule.Parent = tune

	local unitsModule = Instance.new("ModuleScript")
	unitsModule.Name = "Units"
	unitsModule.Source = AC.units
	unitsModule.Parent = readmeModule

	-- Initialize.luau iterates this, then destroys it - an empty Plugins folder is intentional:
	-- the optional Gauges/Controls/Sounds/etc. plugins need extra GUI assets this converter can't
	-- build, so they're left out rather than shipped broken.
	local plugins = Instance.new("Folder")
	plugins.Name = "Plugins"
	plugins.Parent = tune

	local interface = Instance.new("ScreenGui")
	interface.Name = "A-Chassis Interface"
	interface.ResetOnSpawn = false
	interface.Enabled = true
	interface.Parent = tune

	local carValue = Instance.new("ObjectValue")
	carValue.Name = "Car"
	carValue.Parent = interface

	local valuesFolder = Instance.new("Folder")
	valuesFolder.Name = "Values"
	valuesFolder.Parent = interface
	for _, spec in ipairs(AC.values) do
		local inst = Instance.new(spec[2])
		inst.Name = spec[1]
		inst.Parent = valuesFolder
	end

	-- Drive.luau sets this Frame's .Visible on every input-type change regardless of Tune.Mobile,
	-- so it must exist as a real GuiObject; Tune.Mobile is turned off (see achassis.py), so none
	-- of its touch-control children ever get indexed.
	local mobile = Instance.new("Frame")
	mobile.Name = "Mobile"
	mobile.BackgroundTransparency = 1
	mobile.Visible = false
	mobile.Parent = interface

	local isOn = Instance.new("BoolValue")
	isOn.Name = "IsOn"
	isOn.Parent = interface
	local starting = Instance.new("BoolValue")
	starting.Name = "Starting"
	starting.Parent = isOn

	local controlsOpen = Instance.new("BoolValue")
	controlsOpen.Name = "ControlsOpen"
	controlsOpen.Parent = interface

	local driveScript = Instance.new("LocalScript")
	driveScript.Name = "Drive"
	driveScript.Source = AC.drive
	driveScript.Parent = interface

	car.PrimaryPart = driveSeat
else
	-- chassis: an invisible box that carries the weight everything else is welded to. It does not
	-- collide, so the body and doors are walk-through and a player can reach any seat directly; the
	-- car still sits on the ground and has physical presence through the wheel colliders below.
	local box = Instance.new("Part")
	box.Name = "ChassisCollider"
	box.Size = Vector3.new(DATA.collider.size[1], DATA.collider.size[2], DATA.collider.size[3])
	box.CFrame = CFrame.new(DATA.collider.center[1], DATA.collider.center[2] + TUNING.SpawnHeight, DATA.collider.center[3])
	box.Transparency = 1
	box.Anchored = true
	box.CanCollide = false
	box.CustomPhysicalProperties = PhysicalProperties.new(0.6, 0.3, 0.1, 1, 1)
	box.Parent = car

	for name, p in pairs(placed) do
		if not wheelPiece[name] then
			weld(p, box)
		end
	end

	-- driver seat
	local seat = Instance.new("VehicleSeat")
	seat.Name = "VehicleSeat"
	seat.Size = Vector3.new(2, 1, 2)
	seat.CFrame = CFrame.new(DATA.seat[1], DATA.seat[2] + TUNING.SpawnHeight, DATA.seat[3])
	seat.Transparency = 1
	seat.Anchored = true
	seat.Massless = true
	seat.CanCollide = false
	seat.HeadsUpDisplay = false
	seat.Parent = car
	weld(seat, box)

	local prompt = Instance.new("ProximityPrompt")
	prompt.Name = "DrivePrompt"
	prompt.ActionText = "Drive"
	prompt.ObjectText = DATA.name
	prompt.HoldDuration = 0
	prompt.MaxActivationDistance = 12
	prompt.RequiresLineOfSight = false
	prompt.Parent = seat

	-- passenger seats (a rough guess at where they'd be: no controls, just somewhere to sit)
	for i, pos in ipairs(DATA.passenger_seats or {}) do
		local pseat = Instance.new("VehicleSeat")
		pseat.Name = "PassengerSeat" .. i
		pseat.Size = Vector3.new(2, 1, 2)
		pseat.CFrame = CFrame.new(pos[1], pos[2] + TUNING.SpawnHeight, pos[3])
		pseat.Transparency = 1
		pseat.Anchored = true
		pseat.Massless = true
		pseat.CanCollide = false
		pseat.HeadsUpDisplay = false
		pseat.Parent = car
		weld(pseat, box)

		local pprompt = Instance.new("ProximityPrompt")
		pprompt.Name = "SitPrompt"
		pprompt.ActionText = "Sit"
		pprompt.ObjectText = DATA.name
		pprompt.HoldDuration = 0
		pprompt.MaxActivationDistance = 10
		pprompt.RequiresLineOfSight = false
		pprompt.Parent = pseat
	end

	-- two parts of the same car must never push against each other
	local function noCollide(a, b)
		local c = Instance.new("NoCollisionConstraint")
		c.Part0 = a
		c.Part1 = b
		c.Parent = a
		return c
	end

	----------------------------------------------------------------------------------------------
	-- wheels
	local driveCount = 0
	for _, w in ipairs(DATA.wheels) do
		if w.drive then
			driveCount = driveCount + 1
		end
	end
	if driveCount == 0 then
		driveCount = 1
	end
	local torque = math.max(2000, box.AssemblyMass * TUNING.Acceleration * 1.5)
	-- only used when DATA.advanced_chassis is true: a per-wheel share of the car's weight to spring
	-- against, stiff enough to hold the car up at rest with some travel left for bumps
	local susStiffness = math.max(4000, box.AssemblyMass * 30)
	local susDamping = math.max(150, box.AssemblyMass * 1.5)

	for _, w in ipairs(DATA.wheels) do
		local visible = {}
		for _, n in ipairs(w.pieces) do
			if placed[n] ~= nil then
				table.insert(visible, placed[n])
			end
		end
		if #visible > 0 then
			-- the physical wheel is an invisible cylinder: perfectly round and cheap to simulate, so an
			-- odd tyre mesh can never make the car wobble or jump. The real meshes ride on it.
			local primary = Instance.new("Part")
			primary.Name = "WheelCollider_" .. w.slot
			primary.Shape = Enum.PartType.Cylinder
			primary.Size = Vector3.new(math.max(w.width * 0.9, 0.5), w.radius * 2, w.radius * 2)
			primary.Transparency = 1
			primary.Anchored = true
			primary.CanCollide = true
			primary.CustomPhysicalProperties = PhysicalProperties.new(0.7, 2.2, 0, 100, 1)
			primary.CFrame = CFrame.new(w.hub[1], w.hub[2] + TUNING.SpawnHeight, w.hub[3])
			primary.Parent = car
			for _, p in ipairs(visible) do
				weld(p, primary)
			end
			noCollide(primary, box)
			noCollide(primary, seat)

			local hub = Vector3.new(w.hub[1], w.hub[2] + TUNING.SpawnHeight, w.hub[3])
			local axle = CFrame.new(hub)                       -- the hinge axis is the world X axis (axle direction)
			local vertical = axle * CFrame.Angles(0, 0, math.rad(90)) -- hinge/slide axis = world Y
			local holder = box

			if DATA.advanced_chassis then
				-- a real suspension upright: it hangs off the chassis on a spring instead of being
				-- bolted straight to it, so the wheel can travel up and down over bumps. Everything
				-- that used to attach straight to the chassis (the steering knuckle, or the wheel
				-- itself if this wheel doesn't steer) now attaches to this upright instead.
				local upright = Instance.new("Part")
				upright.Name = "Upright_" .. w.slot
				upright.Size = Vector3.new(0.6, 0.6, 0.6)
				upright.Transparency = 1
				upright.CanCollide = false
				upright.Anchored = true
				upright.CFrame = axle
				upright.CustomPhysicalProperties = PhysicalProperties.new(3, 0.3, 0, 1, 1)
				upright.Parent = car

				local mountCF = CFrame.new(hub.X, hub.Y + TUNING.SuspensionTravel, hub.Z) * CFrame.Angles(0, 0, math.rad(90))
				local susTop = attach(box, mountCF)
				local susBottom = attach(upright, vertical)

				local slide = Instance.new("PrismaticConstraint")
				slide.Name = "Suspension_" .. w.slot
				slide.Attachment0 = susTop
				slide.Attachment1 = susBottom
				slide.LimitsEnabled = true
				slide.LowerLimit = -TUNING.SuspensionTravel
				slide.UpperLimit = TUNING.SuspensionTravel
				slide.Parent = car

				local spring = Instance.new("SpringConstraint")
				spring.Name = "Spring_" .. w.slot
				spring.Attachment0 = susTop
				spring.Attachment1 = susBottom
				spring.FreeLength = TUNING.SuspensionTravel
				spring.Stiffness = susStiffness
				spring.Damping = susDamping
				spring.Parent = car

				holder = upright
			end

			if w.steer then
				local knuckle = Instance.new("Part")
				knuckle.Name = "Knuckle_" .. w.slot
				knuckle.Size = Vector3.new(1, 1, 1)
				knuckle.Transparency = 1
				knuckle.CanCollide = false
				knuckle.Anchored = true
				knuckle.CFrame = axle
				knuckle.CustomPhysicalProperties = PhysicalProperties.new(3, 0.3, 0, 1, 1)
				knuckle.Parent = car

				local steering = Instance.new("HingeConstraint")
				steering.Name = "Steer_" .. w.slot
				steering.Attachment0 = attach(holder, vertical)
				steering.Attachment1 = attach(knuckle, vertical)
				steering.ActuatorType = Enum.ActuatorType.Servo
				steering.ServoMaxTorque = 1000000
				steering.AngularSpeed = 6
				steering.TargetAngle = 0
				steering:SetAttribute("CarRole", "Steer")
				steering.Parent = car
				holder = knuckle
			end

			local rolling = Instance.new("HingeConstraint")
			rolling.Name = "Wheel_" .. w.slot
			rolling.Attachment0 = attach(holder, axle)
			rolling.Attachment1 = attach(primary, axle)
			rolling.ActuatorType = Enum.ActuatorType.Motor
			rolling.AngularVelocity = 0
			rolling.MotorMaxTorque = w.drive and (torque / driveCount) or 0
			if w.drive then
				rolling:SetAttribute("CarRole", "Drive")
				rolling:SetAttribute("Radius", w.radius)
			else
				rolling:SetAttribute("CarRole", "Free")
			end
			rolling.Parent = car
		else
			warn("car2roblox: wheel " .. w.slot .. " was not found, skipping it")
		end
	end

	car.PrimaryPart = box
	car:SetAttribute("DriveSign", TUNING.DriveSign)
	car:SetAttribute("SteerSign", TUNING.SteerSign)
	car:SetAttribute("MaxSpeed", TUNING.MaxSpeed)
	car:SetAttribute("MaxSteerDegrees", TUNING.MaxSteerDegrees)
	car:SetAttribute("AdvancedChassis", DATA.advanced_chassis == true)
	car:SetAttribute("PeakTorque", torque)

	local driver = Instance.new("Script")
	driver.Name = "CarDriver"
	driver.Source = [==[__DRIVER__]==]
	driver.Parent = car
end

----------------------------------------------------------------------------------------------
-- finish: unanchor everything so the car actually sits under physics once Play is pressed
for _, d in ipairs(car:GetDescendants()) do
	if d:IsA("BasePart") then
		d.Anchored = false
	end
end

if source ~= car and source.Parent ~= nil and #source:GetChildren() == 0 then
	source:Destroy()
end
Selection:Set({ car })
local chassisNote, howTo
if DATA.achassis_real then
	chassisNote = " (real A-Chassis by lisphm, MPL-2.0)"
	howTo = ". Press Play, walk up to the driver's seat and touch/click it to get in - A-Chassis's own on-screen dashboard and controls take over from there."
elseif DATA.advanced_chassis then
	chassisNote = " (A-Chassis style suspension + drivetrain)"
	howTo = ". Press Play, walk up to the car and press E at a seat (Drive for the driver's), then W/S = throttle, A/D = steer."
else
	chassisNote = ""
	howTo = ". Press Play, walk up to the car and press E at a seat (Drive for the driver's), then W/S = throttle, A/D = steer."
end
print("car2roblox: built '" .. car.Name .. "' with " .. #DATA.wheels .. " wheel(s) and " .. (#(DATA.passenger_seats or {}) + 1) .. " seat(s)" .. chassisNote .. howTo)

--[[ Recolouring: every paintable piece carries the attribute CarPaint ("primary", "secondary", ...).
for _, d in ipairs(car:GetDescendants()) do
	if d:GetAttribute("CarPaint") == "primary" then d.Color = Color3.fromRGB(200, 30, 30) end
end
]]
'''


def make_rig_script(data: dict, name: str, glb_name: str) -> str:
    return (_TEMPLATE
            .replace("__VERSION__", __version__)
            .replace("__NAME__", name)
            .replace("__GLB__", glb_name)
            .replace("__DATA__", to_lua(data))
            .replace("__DRIVER__", _DRIVER))
