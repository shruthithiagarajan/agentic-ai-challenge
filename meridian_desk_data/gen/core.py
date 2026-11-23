"""Core seed data: catalog.csv, orders.db, ground truth (mismatches, violations, apex, test orders)."""
import csv, json, random, sqlite3, os, datetime as dt

import os as _os; OUT = _os.environ.get("MERIDIAN_OUT", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "meridian_desk_data"))
os.makedirs(OUT + "/ground_truth", exist_ok=True)
random.seed(20261123)
AS_OF = dt.date(2026, 10, 5)  # "today" inside the fictional world
D = dt.date


def r5(x):
    return int(round(x / 5.0) * 5)


# ------------------------------------------------------------------ customers
CUSTOMERS = [
    ("CUST-0001", "Apex Industries", "Rahul Deshmukh", "Gold", "Pune"),
    ("CUST-0002", "Bharat Pumps & Valves", "Suresh Nair", "Silver", "Coimbatore"),
    ("CUST-0003", "Kaveri Engineering Works", "Anita Rao", "Standard", "Bengaluru"),
    ("CUST-0004", "Sundaram Fabricators", "M. Sundaram", "Standard", "Chennai"),
    ("CUST-0005", "Narmada Chemicals Ltd", "Hetal Patel", "Gold", "Vadodara"),
    ("CUST-0006", "Himalaya Process Systems", "Vikram Negi", "Silver", "Dehradun"),
    ("CUST-0007", "Godavari Agro Machinery", "Prakash Jadhav", "Standard", "Nashik"),
    ("CUST-0008", "Deccan Steelworks", "Imran Qureshi", "Gold", "Hyderabad"),
    ("CUST-0009", "Malabar Marine Services", "Thomas Varghese", "Standard", "Kochi"),
    ("CUST-0010", "Konkan Shipyard Supplies", "Sandeep Gawde", "Silver", "Ratnagiri"),
    ("CUST-0011", "Orion Auto Components", "Neha Kulkarni", "Silver", "Chakan"),
    ("CUST-0012", "Vindhya Cement Works", "Alok Tiwari", "Gold", "Satna"),
    ("CUST-0013", "Ganga Textile Mills", "Farhan Siddiqui", "Standard", "Kanpur"),
    ("CUST-0014", "Thar Mining Equipment", "Mahendra Rathore", "Standard", "Jodhpur"),
    ("CUST-0015", "Sahyadri Power Tools", "Pooja Shinde", "Standard", "Kolhapur"),
    ("CUST-0016", "Brahmaputra Tea Machinery", "Dibyajyoti Das", "Standard", "Guwahati"),
    ("CUST-0017", "Cauvery Water Systems", "Lakshmi Gowda", "Silver", "Mysuru"),
    ("CUST-0018", "Nilgiri Pharma Packaging", "Dr. Arvind Menon", "Silver", "Coonoor"),
    ("CUST-0019", "Rann Salt Works", "Jignesh Solanki", "Standard", "Gandhidham"),
    ("CUST-0020", "Kalinga Fertilizers", "Sasmita Mohanty", "Gold", "Bhubaneswar"),
    ("CUST-0021", "Zenith Boiler Services", "Karan Malhotra", "Standard", "Ahmedabad"),
    ("CUST-0022", "Pragati Hydraulics", "Dilip Vaghasiya", "Silver", "Rajkot"),
    ("CUST-0023", "Surat Waterworks", "Mitesh Desai", "Standard", "Surat"),
    ("CUST-0024", "Mahanadi Paper Mills", "Rekha Chandrakar", "Silver", "Raipur"),
    ("CUST-0025", "Yamuna Food Processing", "Ajay Chauhan", "Standard", "Noida"),
    ("CUST-9001", "Meridian Test Account", "QA Team", "Standard", "Pune"),
]
LIMITS = {"Standard": (500000, 30), "Silver": (1500000, 45), "Gold": (5000000, 60)}
CUST = {}
for cid, name, contact, tier, city in CUSTOMERS:
    dom = "".join(ch for ch in name.lower().split(" ")[0] if ch.isalnum()) + ".example"
    CUST[cid] = dict(id=cid, name=name, contact_name=contact, email=f"{contact.split()[-1].lower().replace('.', '')}@{dom}",
                     city=city, tier=tier, credit_limit=LIMITS[tier][0], credit_days=LIMITS[tier][1])
CUST["CUST-9001"]["email"] = "qa@meridiansupply.example"

# ------------------------------------------------------------------ catalog
CATALOG = []  # dicts: sku,name,category,unit_price,stock,specs
USED = set()
EXPLICIT = set()


def add(prefix, cat, num, name, price, specs, unit="pcs", stock=None):
    sku = f"MS-{prefix}{num:04d}"
    assert sku not in USED, sku
    USED.add(sku)
    specs = dict(specs)
    specs["unit"] = unit
    if stock is not None:
        EXPLICIT.add(sku)
    if stock is None:
        stock = random.choice([random.randint(150, 2500)] * 6 + [random.randint(20, 150)] * 2)
    CATALOG.append(dict(sku=sku, name=name, category=cat, unit_price=price, stock=stock, specs=json.dumps(specs, ensure_ascii=False)))


def rnum(prefix, lo=100, hi=899):
    while True:
        n = random.randint(lo, hi)
        if f"MS-{prefix}{n:04d}" not in USED:
            return n


# ---- Valves (40)
SZ = {"0.5": 0.25, "0.75": 0.4, "1": 0.55, "1.5": 0.75, "2": 1.0, "3": 2.4, "4": 3.6}
VT = {"Ball Valve": 1.0, "Gate Valve": 1.25, "Globe Valve": 1.4, "Check Valve": 0.95, "Butterfly Valve": 1.8}
VM = {"Brass": 1.0, "SS304": 2.4, "SS316": 3.3, "Cast Iron": 1.6, "PVC": 0.35}
v_anchor = [
    (200, 'Brass Ball Valve 2" BSP', 1875, {"size": '2"', "material": "Brass", "connection": "BSP", "pressure": "PN25"}, 640),
    (201, 'Brass Gate Valve 2" BSP', 2240, {"size": '2"', "material": "Brass", "connection": "BSP", "pressure": "PN16"}, 310),
    (150, 'Brass Ball Valve 1.5" BSP', 1320, {"size": '1.5"', "material": "Brass", "connection": "BSP", "pressure": "PN25"}, 880),
    (100, 'Brass Ball Valve 1" BSP', 890, {"size": '1"', "material": "Brass", "connection": "BSP", "pressure": "PN25"}, 1900),
    (420, 'SS316 Ball Valve 2" Flanged', 7450, {"size": '2"', "material": "SS316", "connection": "Flanged ANSI 150", "pressure": "PN40"}, 85),
    (430, "SS304 Butterfly Valve DN100 Wafer", 5200, {"size": "DN100", "material": "SS304", "connection": "Wafer", "pressure": "PN16"}, 40),
    (300, "Cast Iron Gate Valve DN80 Flanged", 6150, {"size": "DN80", "material": "Cast Iron", "connection": "Flanged", "pressure": "PN16"}, 0),
    (205, 'PVC Ball Valve 2" Threaded', 655, {"size": '2"', "material": "PVC", "connection": "Threaded", "pressure": "PN10"}, 1200),
]
names = set()
for n, nm, p, sp, st in v_anchor:
    add("VLV", "Valves", n, nm, p, sp, stock=st)
    names.add(nm)
combos = []
for t, tf in VT.items():
    for m, mf in VM.items():
        for s, sf in SZ.items():
            end = "Flanged" if m == "Cast Iron" else ("Threaded" if m == "PVC" else "BSP")
            nm = f'{m} {t} {s}" {end}'
            if nm in names or (t == "Butterfly Valve" and float(s) < 2):
                continue
            combos.append((nm, r5(1875 * tf * mf * sf), {"size": f'{s}"', "material": m, "connection": end}))
random.shuffle(combos)
for nm, p, sp in combos[:32]:
    add("VLV", "Valves", rnum("VLV"), nm, p, sp)

# ---- Fittings (30)
FT = {"Elbow 90deg": 1.0, "Elbow 45deg": 1.1, "Tee": 1.4, "Coupling": 0.8, "Reducer": 1.2, "Union": 2.4, "Nipple": 0.7, "Slip-on Flange": 3.5}
FM = {"GI": 1.0, "Brass": 2.2, "SS304": 3.4, "SS316": 4.5, "PVC": 0.4}
FS = {"0.5": 0.5, "0.75": 0.7, "1": 1.0, "1.5": 1.8, "2": 2.8, "3": 6.0, "4": 9.0}
f_anchor = [
    (110, 'Brass Elbow 90deg 1" BSP', 185, {"size": '1"', "material": "Brass"}),
    (220, 'GI Tee 2" BSP', 240, {"size": '2"', "material": "GI"}),
    (330, 'SS304 Union 1" BSP', 460, {"size": '1"', "material": "SS304"}),
]
fn = set()
for n, nm, p, sp in f_anchor:
    add("FIT", "Fittings", n, nm, p, sp)
    fn.add(nm)
combos = []
for t, tf in FT.items():
    for m, mf in FM.items():
        for s, sf in FS.items():
            nm = f'{m} {t} {s}" BSP'
            if nm in fn:
                continue
            combos.append((nm, max(15, r5(85 * tf * mf * sf)), {"size": f'{s}"', "material": m}))
random.shuffle(combos)
for nm, p, sp in combos[:27]:
    add("FIT", "Fittings", rnum("FIT"), nm, p, sp)

# ---- Fasteners (25), priced per box of 100
SF = {"M8": 0.4, "M10": 0.6, "M12": 1.0, "M16": 1.9, "M20": 3.2, "M24": 5.0}
add("FAS", "Fasteners", 105, "Hex Bolt M12x60 Zinc Plated Gr 8.8 (box of 100)", 1450, {"size": "M12x60", "grade": "8.8", "finish": "Zinc"}, unit="box")
add("FAS", "Fasteners", 110, "Hex Nut M12 Zinc Plated (box of 100)", 620, {"size": "M12", "grade": "8", "finish": "Zinc"}, unit="box")
fcombos = []
for s, sf in SF.items():
    for L in (30, 40, 60, 80):
        fcombos.append((f"Hex Bolt {s}x{L} Zinc Plated Gr 8.8 (box of 100)", r5(1450 * sf * L / 60), {"size": f"{s}x{L}", "grade": "8.8"}))
        fcombos.append((f"Hex Bolt {s}x{L} SS304 (box of 100)", r5(3900 * sf * L / 60), {"size": f"{s}x{L}", "grade": "A2-70"}))
    fcombos.append((f"Hex Nut {s} SS304 (box of 100)", r5(1500 * sf), {"size": s, "grade": "A2-70"}))
    fcombos.append((f"Flat Washer {s} Zinc Plated (box of 100)", r5(180 * sf) or 60, {"size": s}))
    fcombos.append((f"Spring Washer {s} (box of 100)", r5(230 * sf) or 70, {"size": s}))
    fcombos.append((f"Hex Nut {s} Zinc Plated (box of 100)", r5(620 * sf), {"size": s}))
random.shuffle(fcombos)
seen = {c["name"] for c in CATALOG}
k = 0
for nm, p, sp in fcombos:
    if nm in seen or k >= 23:
        continue
    seen.add(nm)
    add("FAS", "Fasteners", rnum("FAS"), nm, p, sp, unit="box")
    k += 1

# ---- PPE (18)
ppe = [
    (101, "Nitrile Coated Gloves Size L (pair)", 95, {"size": "L"}, "pair"),
    (120, "Safety Helmet ISI Yellow", 310, {"colour": "Yellow", "standard": "IS 2925"}, "pcs"),
    (140, "Chemical Splash Goggles", 275, {"type": "indirect vent"}, "pcs"),
    (102, "Nitrile Coated Gloves Size M (pair)", 95, {"size": "M"}, "pair"),
    (103, "Nitrile Coated Gloves Size XL (pair)", 98, {"size": "XL"}, "pair"),
    (104, "Leather Welding Gauntlets (pair)", 340, {"length": "14 in"}, "pair"),
    (105, "Cut Resistant Gloves Level 5 (pair)", 410, {"cut_level": "5"}, "pair"),
    (106, "Chemical Resistant Gloves Neoprene (pair)", 560, {"material": "Neoprene"}, "pair"),
    (121, "Safety Helmet ISI White", 310, {"colour": "White"}, "pcs"),
    (122, "Safety Helmet ISI Blue", 310, {"colour": "Blue"}, "pcs"),
    (141, "Clear Safety Spectacles Anti-fog", 120, {"coating": "anti-fog"}, "pcs"),
    (142, "Welding Face Shield Shade 11", 460, {"shade": "11"}, "pcs"),
    (150, "Half-Mask Respirator with A1 Filters", 1450, {"filters": "A1"}, "pcs"),
    (151, "N95 Particulate Mask (box of 20)", 780, {"rating": "N95"}, "box"),
    (160, "Steel Toe Safety Shoes Size 8", 1290, {"size": "8"}, "pair"),
    (161, "Steel Toe Safety Shoes Size 9", 1290, {"size": "9"}, "pair"),
    (170, "Cotton Coverall Size L", 890, {"size": "L"}, "pcs"),
    (180, "Ear Defenders 28dB", 380, {"nrr": "28dB"}, "pcs"),
]
for n, nm, p, sp, u in ppe:
    add("PPE", "PPE", n, nm, p, sp, unit=u)

# ---- Hazardous Goods (15)
haz = [
    (101, "Industrial Acetone 20 L Drum", 4350, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (102, "Isopropyl Alcohol 99% 20 L Drum", 5100, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (103, "Toluene 20 L Drum", 4900, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (104, "Paint Thinner 20 L Drum", 3250, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (105, "Hydrochloric Acid 30% 25 L Carboy", 3200, {"hazmat_class": "8", "hazard": "corrosive", "volume": "25 L"}, "carboy"),
    (106, "Sulphuric Acid 98% 25 L Carboy", 3650, {"hazmat_class": "8", "hazard": "corrosive", "volume": "25 L"}, "carboy"),
    (107, "Caustic Soda Lye 48% 25 L Carboy", 2950, {"hazmat_class": "8", "hazard": "corrosive", "volume": "25 L"}, "carboy"),
    (108, "Methylated Spirit 20 L Drum", 2700, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (109, "Industrial Degreaser Solvent 20 L Drum", 4100, {"hazmat_class": "3", "hazard": "flammable liquid", "volume": "20 L"}, "drum"),
    (110, "Argon Gas Cylinder 47 L (filled)", 5800, {"hazmat_class": "2.2", "hazard": "compressed gas", "volume": "47 L"}, "cylinder"),
    (111, "Carbon Dioxide Cylinder 30 kg (filled)", 6900, {"hazmat_class": "2.2", "hazard": "compressed gas", "volume": "30 kg"}, "cylinder"),
    (112, "Acetylene Cylinder 7 kg (filled)", 7450, {"hazmat_class": "2.1", "hazard": "flammable gas", "volume": "7 kg"}, "cylinder"),
    (113, "Epoxy Resin and Hardener Kit 20 kg", 8900, {"hazmat_class": "9", "hazard": "environmentally hazardous", "volume": "20 kg"}, "kit"),
    (114, "Industrial Spray Paint Aerosol (carton of 12)", 2250, {"hazmat_class": "2.1", "hazard": "aerosol", "volume": "12 x 400 ml"}, "carton"),
    (115, "Oxygen Gas Cylinder 47 L (filled)", 4200, {"hazmat_class": "2.2", "hazard": "compressed gas (oxidiser)", "volume": "47 L"}, "cylinder"),
]
for n, nm, p, sp, u in haz:
    add("HAZ", "Hazardous Goods", n, nm, p, sp, unit=u)

# ---- Tools (18)
tools = [
    (110, "Torque Wrench 40-200 Nm 1/2 in drive", 7800, {"range": "40-200 Nm"}),
    (120, "Pipe Wrench 18 inch Heavy Duty", 1650, {"length": "18 in"}),
    (101, "Combination Spanner Set 8-24 mm (12 pcs)", 2450, {}),
    (102, "Ring Spanner Set 6-32 mm (12 pcs)", 2950, {}),
    (103, "Adjustable Spanner 12 inch", 560, {}),
    (104, "Bench Vice 150 mm", 2650, {}),
    (105, 'Angle Grinder 4" 850 W', 3150, {"power": "850 W"}),
    (106, "Electric Drill 13 mm 600 W", 3450, {"power": "600 W"}),
    (107, "Hammer Drill 26 mm 800 W", 6250, {"power": "800 W"}),
    (108, "Hacksaw Frame with 10 Blades", 380, {}),
    (111, "Pipe Cutter 1/2 - 2 inch", 890, {}),
    (112, "Pipe Threading Die Set 1/2 - 2 inch", 9400, {}),
    (113, "Socket Set 1/2 in Drive (32 pcs)", 4150, {}),
    (114, "Digital Vernier Caliper 150 mm", 1950, {}),
    (115, "Steel Tape 8 m", 260, {}),
    (116, "Spirit Level 600 mm", 520, {}),
    (117, "Hand Riveter Heavy Duty", 880, {}),
    (118, "Chain Pipe Wrench 2 inch", 1480, {}),
]
for n, nm, p, sp in tools:
    add("TLS", "Tools", n, nm, p, sp)

# ---- Pipes (16)
PF = {"0.5": 0.2, "1": 0.45, "2": 1.0, "3": 2.2, "4": 3.4}
pipes = [(101, 'GI Pipe 2" Class B 6 m', 4150, {"size": '2"', "material": "GI"}),
         (120, 'SS304 Pipe 1" Sch40 6 m', 6900, {"size": '1"', "material": "SS304"})]
pn = {p[1] for p in pipes}
for s, f in PF.items():
    for nm, base, m in ((f'GI Pipe {s}" Class B 6 m', 4150, "GI"), (f'SS304 Pipe {s}" Sch40 6 m', 15000, "SS304")):
        if nm not in pn and len(pipes) < 12:
            pipes.append((rnum("PIP", 200, 899), nm, r5(base * f), {"size": f'{s}"', "material": m}))
            pn.add(nm)
for s in ("1", "2", "3", "4"):
    pipes.append((rnum("PIP", 300, 899), f'PVC Pressure Pipe {s}" 6 m', r5(2400 * PF.get(s, 1)), {"size": f'{s}"', "material": "PVC"}))
pipes = pipes[:14]
pipes += [(rnum("PIP", 400, 899), "HDPE PE100 Coil 63 mm 50 m", 6200, {"size": "63 mm"}), (rnum("PIP", 400, 899), "HDPE PE100 Coil 90 mm 50 m", 12400, {"size": "90 mm"})]
USED_PIP = set()
for n, nm, p, sp in pipes:
    if f"MS-PIP{n:04d}" in USED:
        n = rnum("PIP")
    add("PIP", "Pipes", n, nm, p, sp, unit="length")

# ---- Bearings (12)
brg = [(101, "Deep Groove Ball Bearing 6205-2RS", 285, 1800), (110, "Tapered Roller Bearing 32008", 1180, 5),
       (102, "Deep Groove Ball Bearing 6203-2RS", 210, None), (103, "Deep Groove Ball Bearing 6204-2RS", 245, None),
       (104, "Deep Groove Ball Bearing 6206-2RS", 340, None), (105, "Deep Groove Ball Bearing 6305-2RS", 520, None),
       (106, "Deep Groove Ball Bearing 6308-2RS", 880, None), (111, "Tapered Roller Bearing 30205", 460, None),
       (120, "Spherical Roller Bearing 22210", 2650, None), (130, "Thrust Ball Bearing 51108", 690, None),
       (140, "Cylindrical Roller Bearing NU208", 1320, None), (150, "Pillow Block Bearing UCP205", 780, None)]
for n, nm, p, st in brg:
    add("BRG", "Bearings", n, nm, p, {"designation": nm.split(" ")[-1]}, stock=st)

# ---- Electrical (12)
elc = [(410, "3-Phase Induction Motor 7.5 kW 1440 rpm", 42500), (420, "Contactor 32A 3-Pole 230V Coil", 1450),
       (401, "3-Phase Induction Motor 1.5 kW 1440 rpm", 9800), (402, "3-Phase Induction Motor 3 kW 1440 rpm", 15400),
       (403, "3-Phase Induction Motor 5.5 kW 1440 rpm", 31200), (404, "3-Phase Induction Motor 11 kW 1440 rpm", 58900),
       (421, "Contactor 9A 3-Pole 230V Coil", 680), (430, "MCB 16A 3-Pole C Curve", 910), (431, "MCCB 100A 3-Pole", 4850),
       (440, "Thermal Overload Relay 12-18A", 1150), (450, "Armoured Cable 4 Core 2.5 sq mm (100 m)", 11400), (460, "Push Button Station 3-Button", 640)]
for n, nm, p in elc:
    add("ELC", "Electrical", n, nm, p, {})

# ---- Lubricants (8) and Gaskets (6)
lub = [(101, "Lithium Grease EP2 18 kg Pail", 6100), (102, "Grease Cartridge EP2 400 g (box of 12)", 1450), (110, "Gear Oil EP 220 20 L Pail", 4800),
       (111, "Hydraulic Oil ISO 68 20 L Pail", 3900), (112, "Compressor Oil ISO 100 20 L Pail", 4400), (120, "Chain Lubricant Spray (carton of 12)", 2150),
       (121, "Copper Anti-Seize Paste 500 g", 640), (122, "Penetrating Oil 5 L", 1250)]
for n, nm, p in lub:
    add("LUB", "Lubricants", n, nm, p, {})
gsk = [(101, "Spiral Wound Gasket DN80 PN16", 520, 0), (102, "Spiral Wound Gasket DN100 PN16", 690, None), (110, "PTFE Thread Seal Tape (pack of 10)", 380, None),
       (120, "Rubber Sheet 3 mm 1 m x 1 m", 1150, None), (130, "Graphite Packing Rope 10 mm 5 m", 860, None), (140, "O-Ring Assortment Kit 382 pcs", 1650, None)]
for n, nm, p, st in gsk:
    add("GSK", "Gaskets & Seals", n, nm, p, {}, stock=st)

assert len(CATALOG) == 200, len(CATALOG)
# some more out-of-stock / low-stock realism
cands = [c for c in CATALOG if c["stock"] > 150 and c["sku"] not in EXPLICIT and c["sku"] not in {"MS-VLV0200", "MS-VLV0201", "MS-VLV0100", "MS-ELC0410", "MS-ELC0420", "MS-FAS0105", "MS-FAS0110", "MS-HAZ0101", "MS-HAZ0105", "MS-PPE0120", "MS-PPE0101", "MS-TLS0110", "MS-PIP0120", "MS-VLV0420", "MS-VLV0430", "MS-FIT0220"}]
random.shuffle(cands)
for c in cands[:8]:
    c["stock"] = 0
for c in cands[8:18]:
    c["stock"] = random.randint(2, 18)
STOCKFIX = {"MS-PIP0120": 120, "MS-FIT0220": 900, "MS-FIT0110": 4200, "MS-PPE0120": 800, "MS-PPE0101": 1500, "MS-TLS0110": 60,
            "MS-ELC0410": 14, "MS-ELC0420": 220, "MS-FAS0105": 600, "MS-FAS0110": 700, "MS-HAZ0101": 90, "MS-HAZ0105": 60}
for c in CATALOG:
    if c["sku"] in STOCKFIX:
        c["stock"] = STOCKFIX[c["sku"]]
CAT = {c["sku"]: c for c in CATALOG}
PRICE = {s: c["unit_price"] for s, c in CAT.items()}
UNIT = {s: json.loads(c["specs"])["unit"] for s, c in CAT.items()}
CATEGORY = {s: c["category"] for s, c in CAT.items()}
CATALOG.sort(key=lambda c: c["sku"])
with open(OUT + "/catalog.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["sku", "name", "category", "unit_price", "stock", "specs"])
    w.writeheader()
    w.writerows(CATALOG)


# ------------------------------------------------------------------ orders
def tier_disc(q):
    return 0.15 if q >= 500 else 0.12 if q >= 250 else 0.06 if q >= 100 else 0.03 if q >= 50 else 0.0


def exp_price(sku, q):
    return round(PRICE[sku] * (1 - tier_disc(q)), 2)


APPROVED_HAZ = ["SafeHaul Logistics", "VRL Logistics"]
CARRIERS = ["BlueDart", "Delhivery", "DTDC", "VRL Logistics", "SafeHaul Logistics"]
CODE = {"BlueDart": "BD", "Delhivery": "DL", "DTDC": "DT", "VRL Logistics": "VR", "SafeHaul Logistics": "SH"}
ALL_SKUS = [c["sku"] for c in CATALOG]
NOTES = ["", "", "", "", "Deliver to Gate 2 between 9-5", "Call security before delivery", "Urgent - plant shutdown next week", "Include test certificate with consignment",
         "Please pack separately", "Billing address differs from shipping address", "Repeat order", "Handle with care"]


def make_lines(cid, force=None):
    cap = 0.6 * CUST[cid]["credit_limit"]
    for _ in range(50):
        n = random.choices([1, 2, 3, 4, 5], [35, 30, 20, 10, 5])[0]
        lines = []
        for s in random.sample(ALL_SKUS, n):
            p = PRICE[s]
            if p >= 20000:
                q = random.randint(1, 4)
            elif p >= 5000:
                q = random.randint(1, 15)
            elif p >= 1000:
                q = random.randint(1, 30)
            else:
                q = random.choice([random.randint(1, 40)] * 5 + [random.choice([55, 60, 75, 80, 100, 120, 150, 180, 260, 300, 340, 400])])
            if CATEGORY[s] == "Hazardous Goods":
                q = random.randint(1, 10)
            lines.append((s, q))
        total = sum(q * exp_price(s, q) for s, q in lines)
        if total <= cap:
            return lines
    return [(random.choice(ALL_SKUS[:3]), 1)]


def mk_order(oid, cid, odate, lines, status=None, carrier=None, ship_lag=None, late=0, notes=None):
    lead = random.randint(4, 7)
    promised = odate + dt.timedelta(days=lead)
    ship_lag = random.randint(0, 2) if ship_lag is None else ship_lag
    shipped = odate + dt.timedelta(days=ship_lag)
    delivered = shipped + dt.timedelta(days=random.randint(2, 5)) + dt.timedelta(days=late)
    has_haz = any(CATEGORY[s] == "Hazardous Goods" for s, _ in lines)
    carrier = carrier or random.choice(APPROVED_HAZ if has_haz else CARRIERS)
    o = dict(id=oid, customer_id=cid, status=status, order_date=odate, promised_date=promised, shipped_date=shipped, delivered_date=delivered,
             carrier=carrier, tracking_no=f"{CODE[carrier]}{random.randint(10**8, 10**9 - 1)}", notes=notes if notes is not None else random.choice(NOTES),
             refund_amount=None, refund_approved_by=None, lines=[dict(sku=s, quantity=q, unit=UNIT[s], price=exp_price(s, q)) for s, q in lines])
    if o["notes"] == "" and random.random() < 0.3:
        o["notes"] = f"PO ref: PO-{random.randint(1000, 9999)}"
    return o


def total(o):
    return round(sum(l["quantity"] * l["price"] for l in o["lines"]), 2)


SPECIAL = {}
APEX_SPEC = [  # id, days_delayed, lines
    (10388, 6, [("MS-VLV0200", 20), ("MS-FIT0110", 40)]),
    (10401, 7, [("MS-VLV0300", 4), ("MS-GSK0101", 10)]),
    (10415, 9, [("MS-BRG0101", 60), ("MS-BRG0110", 30)]),
    (10433, 11, [("MS-VLV0420", 6), ("MS-PIP0120", 10)]),
    (10447, 14, [("MS-FAS0105", 15), ("MS-FAS0110", 15)]),
    (10452, 17, [("MS-ELC0410", 3), ("MS-ELC0420", 6)]),
    (10463, 21, [("MS-VLV0300", 2), ("MS-VLV0430", 3)]),
    (10471, 26, [("MS-TLS0110", 2), ("MS-GSK0101", 20)]),
    (10478, 3, [("MS-HAZ0101", 5), ("MS-FIT0220", 30)]),
    (10485, 4, [("MS-PPE0120", 100), ("MS-PPE0101", 200)]),
]
for oid, dd, lines in APEX_SPEC:
    promised = AS_OF - dt.timedelta(days=dd)
    o = mk_order(f"MS-{oid}", "CUST-0001", promised - dt.timedelta(days=6), lines, status="delayed", notes="Plant maintenance window - need on time")
    o.update(promised_date=promised, shipped_date=None, delivered_date=None, carrier=None, tracking_no=None)
    SPECIAL[o["id"]] = o
for oid, cid, dd, lines in [(10360, "CUST-0021", 8, [("MS-VLV0200", 10)]), (10372, "CUST-0013", 3, [("MS-FIT0330", 30), ("MS-GSK0102", 20)])]:
    promised = AS_OF - dt.timedelta(days=dd)
    o = mk_order(f"MS-{oid}", cid, promised - dt.timedelta(days=6), lines, status="delayed", notes="")
    o.update(promised_date=promised, shipped_date=None, delivered_date=None, carrier=None, tracking_no=None)
    SPECIAL[o["id"]] = o
o = mk_order("MS-10422", "CUST-0003", D(2026, 9, 29), [("MS-VLV0100", 40), ("MS-FIT0110", 25)], status="shipped", carrier="Delhivery", ship_lag=3, notes="")
o.update(promised_date=D(2026, 10, 6), shipped_date=D(2026, 10, 2), delivered_date=None)
SPECIAL["MS-10422"] = o
# test orders + decoys
for i, oid in enumerate(range(10491, 10497)):
    o = mk_order(f"MS-{oid}", "CUST-9001", D(2026, 9, 20 + i), [(random.choice(ALL_SKUS[:50]), 1)], status="pending", notes=["TEST ORDER - safe to delete", "QA test - do not fulfil", "test", "TEST - checkout flow regression", "Test order from staging", "dummy order for UAT"][i])
    o.update(shipped_date=None, delivered_date=None, carrier=None, tracking_no=None)
    SPECIAL[o["id"]] = o
for oid, cid in ((10312, "CUST-0008"), (10345, "CUST-0012")):
    SPECIAL[f"MS-{oid}"] = mk_order(f"MS-{oid}", cid, D(2026, 8, 20 if oid == 10312 else 9, 1 if False else 4) if False else (D(2026, 8, 20) if oid == 10312 else D(2026, 9, 4)),
                                   [("MS-FIT0220", 12)], status="delivered", notes="Test certificate required with delivery (real order - do not delete)")

ids_all = [f"MS-{i}" for i in range(10001, 10501)]
free_ids = [i for i in ids_all if i not in SPECIAL]
days = [D(2026, 7, 1) + dt.timedelta(days=d) for d in range(92)]
dates = sorted(random.choice(days) for _ in free_ids)
ORDERS = {}
for oid, odate in zip(free_ids, dates):
    cid = random.choices([c[0] for c in CUSTOMERS[:-1]], [4 if c[0] == "CUST-0001" else 2 if c[3] == "Gold" else 1 for c in CUSTOMERS[:-1]])[0]
    late = random.choices([0, random.randint(1, 5), random.randint(6, 12)], [85, 12, 3])[0]
    o = mk_order(oid, cid, odate, make_lines(cid), late=late)
    if odate >= D(2026, 9, 29):
        o["status"] = random.choice(["pending", "processing"])
        o.update(shipped_date=None, delivered_date=None, carrier=None, tracking_no=None)
    elif o["delivered_date"] > AS_OF:
        o["status"] = "shipped"
        o["delivered_date"] = None
    else:
        r = random.random()
        o["status"] = "cancelled" if r < 0.04 else "refunded" if r < 0.07 else "delivered"
        if o["status"] == "cancelled":
            o.update(shipped_date=None, delivered_date=None, carrier=None, tracking_no=None)
    ORDERS[oid] = o
ORDERS.update(SPECIAL)

# normal refunds
for o in ORDERS.values():
    if o["status"] == "refunded" and o["id"] not in SPECIAL:
        t = total(o)
        if t > 6000 and random.random() < 0.3:
            o["refund_amount"] = float(random.randint(5100, min(9500, int(t))))
            o["refund_approved_by"] = random.choice(["mgr.sharma", "mgr.iyer"])
        else:
            o["refund_amount"] = float(round(min(t * random.uniform(0.3, 1.0), 4800), 2))

# ---------------- planted issues
elig = [o for o in ORDERS.values() if o["status"] == "delivered" and o["id"] not in SPECIAL and "MS-10020" < o["id"] < "MS-10440"]
random.shuffle(elig)
cheap = [s for s in ALL_SKUS if PRICE[s] <= 500 and CATEGORY[s] != "Hazardous Goods"]
MISMATCHES = []
mm_specs = [("overcharge", 1.10, None), ("overcharge", 1.06, None), ("undercharge", 0.88, None), ("undercharge", 0.92, None),
            ("stale_price", 0.93, None), ("stale_price", 1.07, None),
            ("bad_discount", None, (30, 0.03)), ("bad_discount", None, (120, 0.12)), ("bad_discount", None, (300, 0.06)), ("bad_discount", None, (60, 0.0))]
for (typ, mult, bd), o in zip(mm_specs, elig[:10]):
    if bd is None:
        l = o["lines"][0]
        q = l["quantity"] if l["quantity"] < 50 else random.randint(2, 40)  # keep qty below any bulk tier so the multiplier is unambiguous
        l["quantity"] = q
        l["price"] = round(PRICE[l["sku"]] * mult, 2)
        expected = exp_price(l["sku"], q)
    else:
        q, disc = bd
        s = random.choice(cheap)
        o["lines"][0] = dict(sku=s, quantity=q, unit=UNIT[s], price=round(PRICE[s] * (1 - disc), 2))
        l = o["lines"][0]
        expected = exp_price(s, q)
    o["_mm"] = True
    MISMATCHES.append(dict(order_id=o["id"], sku=l["sku"], quantity=l["quantity"], catalog_price=PRICE[l["sku"]], expected_unit_price=expected,
                           charged_price=l["price"], mismatch_type=typ,
                           note={"overcharge": "Charged above catalogue price", "undercharge": "Charged below catalogue price", "stale_price": "Charged a stale (pre-revision) price",
                                 "bad_discount": f"Bulk discount applied wrongly (qty {l['quantity']}, tier should give {tier_disc(l['quantity']):.0%}, charged {1 - l['price'] / PRICE[l['sku']]:.0%})"}[typ]))
VIOL = []
rest = elig[10:]
for o, carrier in zip(rest[:5], ["Delhivery", "DTDC", "BlueDart", "DTDC", "Delhivery"]):
    s = random.choice([c["sku"] for c in CATALOG if c["category"] == "Hazardous Goods"])
    q = random.randint(2, 8)
    o["lines"][0] = dict(sku=s, quantity=q, unit=UNIT[s], price=exp_price(s, q))
    o["carrier"] = carrier
    o["tracking_no"] = f"{CODE[carrier]}{random.randint(10**8, 10**9 - 1)}"
    VIOL.append(dict(order_id=o["id"], violation_type="hazardous_goods_wrong_carrier", policy="hazardous_goods.md#2", detail=f"{s} shipped via {carrier}; approved carriers: SafeHaul Logistics, VRL Logistics"))
std = [c for c in CUSTOMERS if c[3] == "Standard" and c[0] != "CUST-9001"]
for o, c in zip(rest[5:8], std[:3]):
    o["customer_id"] = c[0]
    o["lines"] = [dict(sku="MS-ELC0410", quantity=13, unit="pcs", price=42500.0)]
    VIOL.append(dict(order_id=o["id"], violation_type="credit_limit_exceeded", policy="credit_terms.md#2", detail=f"Order total 552500 exceeds Standard-tier credit limit 500000 for {c[0]}"))
for o, amt, appr in zip(rest[8:10], [7500.0, 12400.0], [None, "mgr.sharma"]):
    o["lines"] = [dict(sku="MS-TLS0110", quantity=2, unit="pcs", price=7800.0)]
    o["status"] = "refunded"
    o["refund_amount"], o["refund_approved_by"] = amt, appr
    VIOL.append(dict(order_id=o["id"], violation_type="refund_without_approval" if appr is None else "refund_exceeds_cap", policy="refund_approval.md#2",
                     detail=f"Refund {amt:.0f} " + ("above 5000 with no manager approval" if appr is None else "above 10000 hard cap (Finance Controller only)")))

# ---------------- write DB
db = OUT + "/orders.db"
if os.path.exists(db):
    os.remove(db)
con = sqlite3.connect(db)
con.executescript("""
CREATE TABLE customers(id TEXT PRIMARY KEY, name TEXT, contact_name TEXT, email TEXT, city TEXT, tier TEXT, credit_limit INTEGER, credit_days INTEGER);
CREATE TABLE orders(id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), status TEXT, order_date TEXT, promised_date TEXT, shipped_date TEXT, delivered_date TEXT,
  carrier TEXT, tracking_no TEXT, total REAL, notes TEXT, refund_amount REAL, refund_approved_by TEXT);
CREATE TABLE order_lines(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT REFERENCES orders(id), sku TEXT, quantity INTEGER, unit TEXT, unit_price_charged REAL);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE refund_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, amount REAL, idempotency_key TEXT UNIQUE, approved_by TEXT, created_at TEXT);
CREATE INDEX idx_orders_customer ON orders(customer_id); CREATE INDEX idx_lines_order ON order_lines(order_id);
""")
con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", [(c["id"], c["name"], c["contact_name"], c["email"], c["city"], c["tier"], c["credit_limit"], c["credit_days"]) for c in CUST.values()])
iso = lambda d: d.isoformat() if d else None
for oid in sorted(ORDERS):
    o = ORDERS[oid]
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (oid, o["customer_id"], o["status"], iso(o["order_date"]), iso(o["promised_date"]), iso(o["shipped_date"]),
                iso(o["delivered_date"]), o["carrier"], o["tracking_no"], total(o), o["notes"], o["refund_amount"], o["refund_approved_by"]))
    con.executemany("INSERT INTO order_lines(order_id,sku,quantity,unit,unit_price_charged) VALUES (?,?,?,?,?)", [(oid, l["sku"], l["quantity"], l["unit"], l["price"]) for l in o["lines"]])
con.execute("INSERT INTO meta VALUES ('as_of_date', ?)", (AS_OF.isoformat(),))
con.execute("INSERT INTO meta VALUES ('currency','INR')")
con.commit()

# ---------------- ground truth
GT = OUT + "/ground_truth/"
json.dump(MISMATCHES, open(GT + "planted_mismatches.json", "w"), indent=2)
json.dump(VIOL, open(GT + "planted_policy_violations.json", "w"), indent=2)


def credit_pct(d):
    return 0 if d <= 5 else 3 if d <= 10 else 5 if d <= 20 else 8


apex = []
for oid, dd, lines in APEX_SPEC:
    o = ORDERS[f"MS-{oid}"]
    ls = [dict(sku=l["sku"], quantity=l["quantity"], stock_now=CAT[l["sku"]]["stock"], fully_in_stock=CAT[l["sku"]]["stock"] >= l["quantity"]) for l in o["lines"]]
    pct = credit_pct(dd)
    apex.append(dict(order_id=o["id"], customer_id="CUST-0001", days_delayed=dd, promised_date=iso(o["promised_date"]), order_total=total(o), delayed_more_than_5_days=dd > 5,
                     lines=ls, all_lines_back_in_stock=all(l["fully_in_stock"] for l in ls), policy_clause="shipping_delays.md#3", expected_credit_percent=pct,
                     expected_credit_amount=round(total(o) * pct / 100, 2)))
json.dump(apex, open(GT + "apex_delayed_orders.json", "w"), indent=2)
json.dump(dict(test_orders=[f"MS-{i}" for i in range(10491, 10497)], decoys_real_orders_with_word_test=["MS-10312", "MS-10345"],
               note="Test orders belong to CUST-9001. Decoys are real customer orders whose notes mention 'test certificate'."), open(GT + "test_orders.json", "w"), indent=2)
json.dump(dict(as_of=AS_OF.isoformat(), bulk_tiers={"50-99": 0.03, "100-249": 0.06, "250-499": 0.12, "500+": 0.15}), open(GT + "world_constants.json", "w"), indent=2)
print("core ok:", len(CATALOG), "products,", len(ORDERS), "orders,", len(MISMATCHES), "mismatches,", len(VIOL), "violations")
