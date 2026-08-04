local ddd_path = os.getenv("DDD_FILE")
local xml_path = os.getenv("DDD_XML")
local tacho_lua = os.getenv("TACHO_LUA")

if not ddd_path or ddd_path == "" then
  log.print(log.ERROR, "DDD_FILE is not set")
  os.exit(1)
end

if not xml_path or xml_path == "" then
  log.print(log.ERROR, "DDD_XML is not set")
  os.exit(1)
end

if not tacho_lua or tacho_lua == "" then
  log.print(log.ERROR, "TACHO_LUA is not set")
  os.exit(1)
end

card.connect = function()
  return false
end

dofile(tacho_lua)

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then
    log.print(log.ERROR, "Cannot open DDD file: " .. path)
    os.exit(1)
  end
  local data = f:read("*a")
  f:close()
  return data
end

local raw = read_file(ddd_path)
local idx = 1
local data_blocks = {}
local sig_blocks = {}

while idx + 4 <= #raw do
  local b1, b2 = raw:byte(idx, idx + 1)
  local dtype = raw:byte(idx + 2)
  local len = raw:byte(idx + 3) * 256 + raw:byte(idx + 4)
  idx = idx + 5
  local payload = raw:sub(idx, idx + len - 1)
  idx = idx + len
  local ef = string.format("%02X%02X", b1, b2)
  if dtype == 0 then
    data_blocks[ef] = payload
  elseif dtype == 1 then
    sig_blocks[ef] = payload
  end
end

local function replace_count(tbl, func, value)
  if not value then
    return
  end
  for _, entry in ipairs(tbl) do
    if type(entry) == "table" then
      if entry[3] == func then
        entry[3] = value
      end
      if type(entry[4]) == "table" then
        replace_count(entry[4], func, value)
      end
    end
  end
end

local function set_counts_from_app_id()
  local app = data_blocks["0501"]
  if not app then
    return
  end
  local b = bytes.new_from_chars(app)
  local counts = {
    noOfEventsPerType = b[3],
    noOfFaultsPerType = b[4],
    activityStructureLength = b[5] * 256 + b[6],
    noOfCardVehicleRecords = b[7] * 256 + b[8],
    noOfCardPlaceRecords = b[9],
  }
  replace_count(TACHO_MAP["EF_Events_Data"], Count_NoOfEventsPerType, counts.noOfEventsPerType)
  replace_count(TACHO_MAP["EF_Faults_Data"], Count_NoOfFaultsPerType, counts.noOfFaultsPerType)
  replace_count(TACHO_MAP["EF_Driver_Activity_Data"], Count_ActivityStructureLength, counts.activityStructureLength)
  replace_count(TACHO_MAP["EF_Vehicles_Used"], Count_NoOfCardVehicleRecords, counts.noOfCardVehicleRecords)
  replace_count(TACHO_MAP["EF_Places"], Count_NoOfCardPlaceRecords, counts.noOfCardPlaceRecords)
end

set_counts_from_app_id()

local root = nodes.root()
local card_node = root:append({ classname = "card", label = "Tachograph" })
local tacho_node = card_node:append({ classname = "application", label = "DF_Tachograph", id = "#FF544143484F" })

local function add_file(parent, ef, name)
  local payload = data_blocks[ef]
  if not payload then
    return nil
  end
  local node = parent:append({ classname = "file", label = name, id = "." .. ef })
  local b = bytes.new_from_chars(payload)
  tacho_map(name, b, node)
  return node
end

add_file(card_node, "0002", "EF_ICC")
add_file(card_node, "0005", "EF_IC")

add_file(tacho_node, "0501", "EF_Application_Identification")
add_file(tacho_node, "C100", "EF_Card_Certificate")
add_file(tacho_node, "C108", "EF_CA_Certificate")
add_file(tacho_node, "0520", "EF_Identification")
add_file(tacho_node, "050E", "EF_Card_Download")
add_file(tacho_node, "0521", "EF_Driving_Licence_info")
add_file(tacho_node, "0502", "EF_Events_Data")
add_file(tacho_node, "0503", "EF_Faults_Data")
add_file(tacho_node, "0504", "EF_Driver_Activity_Data")
add_file(tacho_node, "0505", "EF_Vehicles_Used")
add_file(tacho_node, "0506", "EF_Places")
add_file(tacho_node, "0507", "EF_Current_Usage")
add_file(tacho_node, "0508", "EF_Control_Activity_Data")
add_file(tacho_node, "0522", "EF_Specific_Conditions")

local xml = nodes.to_xml(nodes.root())
local xf = io.open(xml_path, "w")
if not xf then
  log.print(log.ERROR, "Cannot write XML file: " .. xml_path)
  os.exit(1)
end
xf:write(xml)
xf:close()

os.exit(0)
