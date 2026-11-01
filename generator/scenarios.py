"""Kitchen test scenarios, written like test procedures.

SCENARIOS: list of (rank, slug, steps); test name = NN_slug.
S(description, expected, *actions)   one step; description/expected may span several lines
A(procedure, *checks, tag=None)       one action; each check gives one line of expected/actual result
N(label, nominal, tol, unit, bad)     numeric check: pass when |measured - nominal| <= tol, `bad` = typical faulty value
Q(label, expected, bad)               qualitative check
`tag` links an action to a scripted regression in REGRESSIONS. The first check of a tagged action is the
one that goes wrong for that regression.
"""


def N(label, nominal, tol, unit, bad):
    return ("num", label, nominal, tol, unit, bad)


def Q(label, expected, bad):
    return ("txt", label, expected, bad)


def A(procedure, *checks, tag=None):
    return {"procedure": procedure, "checks": checks, "tag": tag}


def S(description, expected, *actions):
    return {"description": description, "expected": expected, "actions": actions}


SCENARIOS = [
    (1, "fridge_ingredient_check", [
        S("Inventory scan of the fridge.\n"
          "The vision system identifies every item on shelves 1-4 and in the door racks,\n"
          "then reads the expiry date labels.",
          "Inventory matches the reference load list (42 items).\nNo expired or unidentified item.",
          A("Open the fridge door with the robot arm (door angle 90°).\n"
            "Trigger a full camera scan: shelves 1 to 4, then door racks.\n"
            "Close the door.",
            N("Items identified", 42, 0, "", 31), Q("Unidentified items", "none", "3 on shelf 2 (rear)"),
            tag="fridge_scan"),
          A("Run OCR on all expiry date labels.\nCompare each date with the current date.",
            Q("Expired items", "none", "2 flagged: cream, chives"), N("Labels read", 100, 0, "%", 86))),
        S("Temperature check of the fridge and freezer compartments.\n"
          "Measurements are taken after the door has been closed for 5 minutes.",
          "Fridge 4°C ±1\nFreezer -18°C ±2",
          A("Wait 5 min with the door closed.\nRead the fridge probe (middle shelf).",
            N("Fridge temperature", 4.0, 1.0, "°C", 8.7)),
          A("Read the freezer probe (top drawer).",
            N("Freezer temperature", -18.0, 2.0, "°C", -11.0))),
        S("Shopping list generation.\n"
          "Compare the inventory with tonight's menu and push the missing items to the mobile app.",
          "Shopping list created.\nPush notification received on the paired phone.",
          A("Compute the difference between the menu requirements and the inventory.",
            Q("Computation", "completed", "timeout after 30 s"), N("Missing items", 2, 0, "", 9)),
          A("Push the shopping list to the paired phone.\nWait for the delivery receipt.",
            Q("Push notification", "delivered", "not delivered (HTTP 503)"),
            N("Delivery delay", 1.5, 1.5, "s", 31.0), tag="gateway_push")),
    ]),
    (2, "mixer_brioche_dough", [
        S("Weigh and load the ingredients into the mixer bowl.\n"
          "Recipe: flour 500 g, sugar 60 g, salt 10 g, yeast 15 g, eggs 5, butter 250 g (added later).",
          "All dry ingredients and eggs loaded within tolerance.",
          A("Dispense flour from silo 1 into the bowl on the scale.",
            N("Flour", 500, 5, "g", 472)),
          A("Dispense sugar, salt and yeast from the spice carousel.",
            N("Sugar", 60, 2, "g", 71), N("Salt", 10, 0.5, "g", 13.2), N("Yeast", 15, 1, "g", 15)),
          A("Crack 5 eggs into the bowl with the egg module.\nCheck for shell fragments with the camera.",
            N("Eggs", 5, 0, "", 4), Q("Shell fragments", "none", "1 fragment detected"))),
        S("Knead the dough.\nSpeed 2 for 8 minutes, then incorporate the butter at speed 4.",
          "Smooth, elastic dough.\nWindowpane test passed.\nDough temperature below 26°C.",
          A("Knead at speed 2 for 8 minutes.",
            N("Hook speed", 120, 5, "rpm", 210), N("Motor current", 2.1, 0.4, "A", 2.2), tag="mixer_speed"),
          A("Add 250 g of cold butter in 5 portions, one every 30 s.\nKnead at speed 4 for 6 minutes.",
            Q("Butter incorporation", "complete, no lumps", "butter lumps remaining"),
            N("Dough temperature", 24, 2, "°C", 29), tag="mixer_speed"),
          A("Stretch a small piece of dough with the gripper (windowpane test).",
            Q("Gluten membrane", "translucent, holds", "tears"))),
        S("Proof the dough in the proofing drawer.",
          "Dough doubled in volume after 2 h at 27°C.",
          A("Set the proofing drawer to 27°C and 80% humidity.\nStart a 2 h timer.",
            N("Drawer temperature", 27, 1, "°C", 22.5), N("Humidity", 80, 5, "%", 61)),
          A("After 2 h, measure the dough volume with the depth camera.",
            N("Volume ratio", 2.0, 0.2, "x", 1.3))),
        S("Shape and bake the brioche.\nBraid into a loaf tin, egg-wash, bake at 180°C.",
          "Golden crust.\nCore temperature 92°C at the end of baking.",
          A("Preheat the oven to 180°C (fan mode).\nWait for the preheat-complete signal.",
            N("Cavity temperature", 180, 5, "°C", 163), Q("Preheat signal", "received", "received after timeout"),
            tag="oven_temp"),
          A("Bake for 30 minutes.\nInsert the core probe at minute 25.",
            N("Core temperature", 92, 2, "°C", 78), Q("Crust color", "golden brown", "pale"), tag="oven_temp")),
    ]),
    (3, "oven_beef_wellington", [
        S("Sear the beef fillet.\n1.2 kg centre-cut fillet, seasoned, seared on all 4 sides.",
          "Even brown crust on all sides.\nCore still raw (below 20°C).",
          A("Heat the pan with 2 tbsp oil to 230°C.",
            N("Pan surface temperature", 230, 10, "°C", 190)),
          A("Sear each side 60 s, turning with the tongs module.",
            N("Sides seared", 4, 0, "", 2), N("Core temperature", 15, 4, "°C", 31))),
        S("Prepare the duxelles.\n500 g chestnut mushrooms chopped, cooked until all moisture evaporates.",
          "Dry, spreadable mushroom paste.",
          A("Chop the mushrooms with the blender (pulse mode, 6 pulses).",
            Q("Texture", "fine chop", "puree"), N("Particle size", 3, 1, "mm", 0.5)),
          A("Cook the mushrooms in the pan at medium heat, stirring every 30 s.\n"
            "Stop when the moisture sensor reads below 10%.",
            N("Moisture", 8, 2, "%", 27), N("Cooking time", 12, 3, "min", 22))),
        S("Wrap the fillet.\nLayer prosciutto and duxelles on cling film, roll the fillet,\n"
          "then wrap in puff pastry and chill.",
          "Fillet fully wrapped, seam sealed.\nPastry firm after chilling.",
          A("Lay 8 slices of prosciutto, spread the duxelles evenly.",
            Q("Layer coverage", "even, no gaps", "gaps detected"), N("Duxelles thickness", 4, 1, "mm", 9)),
          A("Roll the fillet in puff pastry, seal the seam with egg wash.",
            Q("Seam", "sealed", "open on 4 cm"), Q("Pastry", "intact", "torn at one end")),
          A("Chill in the fridge for 20 minutes.",
            N("Pastry temperature", 5, 2, "°C", 12))),
        S("Bake the Wellington.\nOven preheated to 200°C, baked 35 min until the core reaches 52°C.",
          "Golden pastry.\nCore temperature 52°C ±2 (medium rare).",
          A("Preheat the oven to 200°C (fan mode).\nWait for the preheat-complete signal.",
            N("Cavity temperature", 200, 5, "°C", 187), Q("Preheat signal", "received", "received after timeout"),
            tag="oven_temp"),
          A("Bake for 35 minutes with the core probe inserted.",
            N("Core temperature", 52, 2, "°C", 41), tag="oven_temp"),
          A("Check the pastry colour with the camera (colour chart index).",
            N("Browning index", 7, 1, "", 4), tag="oven_temp")),
        S("Rest and slice.", "Even pink centre, 8 clean slices.",
          A("Rest 10 minutes on the rack.", N("Core temperature", 55, 2, "°C", 47)),
          A("Slice into 2 cm portions with the slicer module.",
            N("Slices", 8, 0, "", 6), Q("Slice integrity", "clean cuts", "pastry crumbled"))),
    ]),
    (4, "sous_vide_duck_breast", [
        S("Prepare and vacuum-seal the duck breasts.",
          "2 bags sealed below 10 mbar.",
          A("Score the skin in a crosshatch pattern (5 mm spacing, 2 mm depth).",
            N("Cut depth", 2, 0.5, "mm", 4.5)),
          A("Season and vacuum-seal each breast.",
            N("Bag pressure", 6, 4, "mbar", 45), Q("Seal", "airtight", "leak detected"))),
        S("Cook in the water bath.\n57°C for 2 hours.",
          "Bath held at 57°C ±0.5 for 2 hours.",
          A("Heat the bath to 57°C.", N("Bath temperature", 57.0, 0.5, "°C", 54.2)),
          A("Start the cook timer for 2 hours.",
            Q("Timer setting", "02:00:00", "00:20:00 (unit error)"), tag="sous_vide_timer"),
          A("Hold the temperature until the timer ends.\nLog the bath temperature every minute.",
            N("Cook time", 120, 1, "min", 20), N("Max deviation", 0.2, 0.3, "°C", 0.3), tag="sous_vide_timer")),
        S("Finish in the pan.\nSear skin side down until crispy.",
          "Crispy skin.\nCore 57°C, pink flesh.",
          A("Pan-sear skin side down for 3 minutes at 220°C.",
            Q("Skin texture", "crispy", "rubbery"), N("Pan temperature", 220, 10, "°C", 175)),
          A("Probe the core temperature.",
            N("Core temperature", 57, 1, "°C", 39), tag="sous_vide_timer")),
    ]),
    (5, "stove_hollandaise", [
        S("Clarify the butter.\nMelt 250 g of butter gently and remove the milk solids.",
          "Clear, golden butterfat.",
          A("Melt the butter in a saucepan on induction zone 2 at 80°C.",
            N("Butter temperature", 80, 3, "°C", 112)),
          A("Skim the foam and pour off the clear fat, leaving the milk solids.",
            Q("Clarity", "clear", "cloudy"), N("Yield", 200, 10, "g", 160))),
        S("Make the sabayon.\nWhisk 3 yolks with 2 tbsp water over a bain-marie until ribbon stage.",
          "Thick, pale sabayon at ribbon stage.\nNo scrambled egg.",
          A("Set the bain-marie to 65°C.",
            N("Water temperature", 65, 2, "°C", 78), tag="stove_induction"),
          A("Whisk the yolks for 4 minutes.",
            Q("Consistency", "ribbon stage", "scrambled"), N("Sabayon temperature", 62, 3, "°C", 74),
            tag="stove_induction")),
        S("Emulsify and season.",
          "Stable, glossy sauce.\npH 4.5 ±0.3.",
          A("Stream the clarified butter in slowly while whisking (10 ml/s).",
            Q("Emulsion", "stable", "split"), N("Flow rate", 10, 2, "ml/s", 25)),
          A("Season with lemon juice and salt.\nMeasure the pH.",
            N("pH", 4.5, 0.3, "", 3.7))),
    ]),
    (6, "fryer_tempura", [
        S("Prepare the batter.\nIce-cold water, flour and egg, mixed briefly (lumps are fine).",
          "Cold, lumpy batter below 6°C.",
          A("Chill the water to 4°C.", N("Water temperature", 4, 1, "°C", 12)),
          A("Mix the flour in for less than 15 s.",
            N("Mixing time", 11, 4, "s", 45), Q("Texture", "lumpy, light", "smooth, gluey"))),
        S("Heat the frying oil.", "Oil stable at 180°C ±3.",
          A("Heat the oil to 180°C.",
            N("Oil temperature", 180, 3, "°C", 168), tag="fryer_oil_temp"),
          A("Let it stabilise for 2 minutes.\nRecord the temperature drift.",
            N("Drift", 1, 1, "°C", 9), tag="fryer_oil_temp")),
        S("Fry prawns and vegetables in batches of 6 pieces.",
          "Light, crispy, pale golden coating.\nOil content below 5%.",
          A("Dip the prawns in the batter and fry for 2 minutes.",
            Q("Coating", "pale golden, crisp", "greasy, pale"), N("Oil recovery time", 40, 10, "s", 95),
            tag="fryer_oil_temp"),
          A("Fry the vegetables for 90 s.", Q("Texture", "crisp", "soggy")),
          A("Drain on the rack for 1 minute.\nEstimate the oil content (NIR sensor).",
            N("Oil content", 3, 2, "%", 11))),
    ]),
    (7, "steamer_dim_sum", [
        S("Prepare the wrapper dough.\nWheat starch and tapioca with boiling water, rested 10 min.",
          "Smooth dough, 24 wrappers of 8 cm.",
          A("Mix the starches with boiling water using the dough hook.",
            Q("Dough", "smooth", "crumbly"), N("Water temperature", 98, 2, "°C", 82)),
          A("Roll and cut 24 wrappers.",
            N("Wrappers", 24, 0, "", 17), N("Diameter", 8, 0.3, "cm", 9.1))),
        S("Fill and pleat the har gow.", "24 sealed dumplings with 7 pleats.",
          A("Place 12 g of shrimp filling on each wrapper.", N("Filling", 12, 1, "g", 16)),
          A("Pleat 7 folds and seal.", Q("Seal", "closed", "open seams on 5"), N("Pleats", 7, 1, "", 4))),
        S("Steam the dumplings.", "Translucent wrappers.\nCore 75°C.",
          A("Bring the steamer to full steam.", N("Steam temperature", 100, 1, "°C", 88)),
          A("Steam for 6 minutes.", N("Core temperature", 76, 3, "°C", 61))),
    ]),
    (8, "blender_gazpacho", [
        S("Load the vegetables.\nTomatoes 1 kg, cucumber 200 g, red pepper 150 g, garlic 1 clove, bread 50 g.",
          "All ingredients loaded within ±2%.",
          A("Dispense the tomatoes.", N("Tomatoes", 1000, 20, "g", 820)),
          A("Add the cucumber, pepper, garlic and bread.",
            N("Total added", 401, 8, "g", 351), Q("Garlic", "present", "missing"))),
        S("Blend.\n90 s at maximum speed, then emulsify 80 ml olive oil at medium speed.",
          "Smooth soup, stable emulsion.",
          A("Blend for 90 s at maximum speed.",
            N("Particle size", 0.3, 0.2, "mm", 2.1), N("Motor speed", 24000, 1500, "rpm", 15000),
            tag="blender_motor"),
          A("Stream in the olive oil at medium speed.", Q("Emulsion", "stable", "oil separated"))),
        S("Chill the gazpacho.", "Served at 6°C.",
          A("Transfer to the fridge.", Q("Fridge door event", "detected", "not detected")),
          A("Chill for 2 hours.", N("Soup temperature", 6, 1, "°C", 11))),
    ]),
    (9, "grill_ribeye", [
        S("Temper and season the steak.\n350 g ribeye, 3 cm thick.",
          "Steak at room temperature, seasoned with 1% salt.",
          A("Rest the steak 45 minutes at room temperature.", N("Core temperature", 18, 2, "°C", 9)),
          A("Season with salt and pepper.", N("Salt", 3.5, 0.3, "g", 8.0))),
        S("Grill to medium rare.", "Crosshatch marks.\nCore 54°C ±1.",
          A("Preheat the grill to 260°C.", N("Grate temperature", 260, 10, "°C", 215)),
          A("Grill 4 min per side, rotating 90° after 2 min.",
            Q("Grill marks", "crosshatch", "no marks"), N("Rotations", 4, 0, "", 2)),
          A("Probe the core.", N("Core temperature", 54, 1, "°C", 63))),
        S("Rest the steak.", "Juices redistributed, core 56°C.",
          A("Rest 8 minutes on the board.", N("Core temperature", 56, 1.5, "°C", 49))),
    ]),
    (10, "oven_souffle", [
        S("Prepare 6 ramekins.\nButter with upward strokes and coat with sugar.",
          "All ramekins evenly buttered and sugared.",
          A("Butter the ramekins with upward strokes.", Q("Coating", "even", "patchy")),
          A("Coat with caster sugar and tip out the excess.", N("Coverage", 100, 2, "%", 71))),
        S("Make the chocolate base.\nMelt 150 g dark chocolate, fold in 4 yolks.",
          "Smooth, homogeneous base at 40-50°C.",
          A("Melt the chocolate over the bain-marie.", N("Chocolate temperature", 45, 2, "°C", 58)),
          A("Fold in the yolks one by one.", Q("Texture", "homogeneous", "grainy"))),
        S("Whip the egg whites and fold them in.", "Stiff peaks.\nVolume kept above 90% after folding.",
          A("Whip 6 whites at speed 6 for 3 minutes.",
            Q("Peaks", "stiff", "soft"), N("Whisk speed", 260, 10, "rpm", 170), tag="mixer_speed"),
          A("Fold into the base in 3 additions.", N("Volume kept", 94, 4, "%", 72))),
        S("Bake the soufflés.\n190°C for 12 minutes, door closed.",
          "Risen 4 cm above the rim.\nNo collapse within 60 s of serving.",
          A("Preheat the oven to 190°C.\nWait for the preheat-complete signal.",
            N("Cavity temperature", 190, 5, "°C", 176), tag="oven_temp"),
          A("Bake for 12 minutes without opening the door.",
            N("Rise", 4.0, 0.5, "cm", 1.1), tag="oven_temp"),
          A("Serve within 60 s and monitor the height.", N("Height loss after 60 s", 3, 5, "%", 40))),
    ]),
    (11, "stove_risotto", [
        S("Toast the rice.\nSweat 1 shallot in butter, add 320 g carnaroli rice.",
          "Shallot translucent, rice edges translucent.",
          A("Sweat the shallot at medium heat for 3 minutes.",
            Q("Shallot", "translucent, no browning", "browned"), N("Pan temperature", 120, 10, "°C", 165),
            tag="stove_induction"),
          A("Add the rice and toast for 2 minutes.", Q("Rice", "translucent edges", "raw"))),
        S("Cook the risotto.\nDeglaze with 100 ml white wine, then add hot stock ladle by ladle for 18 min.",
          "Rice al dente, creamy texture.",
          A("Deglaze with the wine and stir until absorbed.", Q("Wine", "absorbed", "not absorbed")),
          A("Add 1 ladle of stock every 90 s for 18 minutes, stirring.",
            N("Simmer temperature", 95, 3, "°C", 104), Q("Rice", "al dente", "overcooked"),
            tag="stove_induction")),
        S("Mantecatura.\nOff the heat, beat in cold butter and parmesan.",
          "Creamy, flowing 'all'onda' texture.",
          A("Beat in 40 g butter and 60 g parmesan for 1 minute.",
            Q("Texture", "creamy wave", "stiff"), N("Spread on plate", 18, 3, "cm", 11))),
    ]),
    (12, "oven_sourdough_bread", [
        S("Feed the levain.", "Starter doubled within 6 hours.",
          A("Feed the starter 1:1:1 (starter, flour, water) and keep at 26°C.",
            N("Rise after 6 h", 2.0, 0.3, "x", 1.2))),
        S("Bulk fermentation.\nAutolyse 1 h, add levain and salt, 4 sets of stretch and folds.",
          "Dough volume +50% after 4 h at 24°C.",
          A("Autolyse flour and water for 1 hour.", Q("Hydration", "uniform", "dry spots")),
          A("Perform 4 sets of stretch and folds, 30 min apart.", Q("Dough strength", "strong", "slack")),
          A("Ferment at 24°C for 4 hours.", N("Volume increase", 50, 10, "%", 20))),
        S("Bake the loaf.\nIn a dutch oven at 250°C, 20 min covered with steam, then 25 min uncovered.",
          "Open crumb.\nCore 98°C, deep brown crust.",
          A("Preheat the oven and dutch oven to 250°C.",
            N("Cavity temperature", 250, 5, "°C", 232), tag="oven_temp"),
          A("Bake covered for 20 minutes with steam injection.",
            Q("Steam injection", "active", "no steam"), N("Humidity", 85, 10, "%", 30), tag="oven_steam"),
          A("Remove the lid and bake 25 minutes more.",
            N("Core temperature", 98, 1, "°C", 89), tag="oven_temp")),
    ]),
    (13, "grill_vegetable_skewers", [
        S("Prepare 12 vegetable skewers.\nCourgette, pepper, red onion and cherry tomatoes cut in 3 cm pieces.",
          "12 skewers with uniform pieces.",
          A("Cut the vegetables into 3 cm pieces.", N("Piece size", 3.0, 0.4, "cm", 4.8)),
          A("Thread the skewers.", N("Skewers", 12, 0, "", 9))),
        S("Grill the skewers on the rotisserie.", "Charred edges, tender vegetables.",
          A("Preheat the grill to 230°C.", N("Grate temperature", 230, 10, "°C", 198)),
          A("Grill for 10 minutes, rotating every 2 minutes.",
            N("Rotations", 5, 0, "", 2), Q("Char", "even", "one side burnt"), tag="grill_rotisserie")),
    ]),
    (14, "sous_vide_creme_brulee", [
        S("Make the custard.\nInfuse 500 ml cream with a vanilla pod, temper into 6 yolks and 100 g sugar.",
          "Smooth custard, 6 jars of 120 g.",
          A("Heat the cream with the vanilla to 80°C.", N("Cream temperature", 80, 2, "°C", 95)),
          A("Temper the yolks with the hot cream while whisking.", Q("Texture", "smooth", "curdled")),
          A("Fill 6 jars and close the lids finger-tight.", N("Jar fill", 120, 5, "g", 95))),
        S("Cook and chill.\n82°C water bath for 1 hour, then chill 4 hours.",
          "Set custard, chilled to 4°C.",
          A("Heat the bath to 82°C.", N("Bath temperature", 82.0, 0.5, "°C", 79.1)),
          A("Cook for 1 hour.",
            Q("Timer setting", "01:00:00", "00:10:00 (unit error)"), N("Cook time", 60, 1, "min", 10),
            tag="sous_vide_timer"),
          A("Chill in the fridge for 4 hours.", N("Custard temperature", 4, 1, "°C", 15))),
        S("Brûlée.\nSprinkle sugar and caramelise with the torch.",
          "Glassy amber crust on all 6 jars.",
          A("Sprinkle 1 tsp of sugar on each custard.", N("Sugar per jar", 5, 1, "g", 9)),
          A("Caramelise with the torch, 20 s per jar.",
            Q("Crust", "amber glass", "burnt"), N("Crust colour index", 6, 1, "", 9))),
    ]),
    (15, "dishwasher_cleanup", [
        S("Load the dishwasher.", "24 plates and 6 pans loaded.",
          A("Load the plates with the robot arm.", N("Plates", 24, 0, "", 19)),
          A("Load the pans on the lower rack.", N("Pans", 6, 0, "", 4))),
        S("Run the intensive wash programme.\nPre-rinse 40°C, main wash 65°C for 45 min, rinse, dry.",
          "Wash temperatures reached.\nDishes dry.",
          A("Pre-rinse at 40°C.", N("Water temperature", 40, 2, "°C", 31)),
          A("Main wash at 65°C for 45 minutes.",
            N("Water temperature", 65, 2, "°C", 52), N("Heater current", 8.7, 0.5, "A", 4.1),
            tag="dishwasher_heater"),
          A("Dry cycle.", N("Residual water", 0.4, 0.4, "g/plate", 3.2))),
        S("Inspect and unload.", "No residue.\nAll dishes stored.",
          A("Inspect every item with the camera.",
            N("Dirty items", 0, 0, "", 5), tag="dishwasher_heater"),
          A("Store the dishes in the cupboards.", Q("Storage", "all stored", "3 misplaced"))),
    ]),
]

PLATFORMS = {"IN": "InHouse kitchen", "OUT": "Outdoor kitchen"}

# KMS (system under test) releases: (version, campaigns on IN, campaigns on OUT)
KMS_VERSIONS = [
    ("1.0.0", 3, 2), ("1.1.0", 3, 2), ("1.2.0", 3, 2), ("1.3.0", 3, 2), ("1.4.0", 2, 1),
    ("1.4.1", 3, 2), ("1.5.0", 3, 2), ("2.0.0", 4, 3), ("2.1.0", 3, 2), ("2.1.1", 2, 2),
    ("2.2.0", 4, 3), ("2.3.0", 3, 2), ("2.4.0", 4, 3),
]

# Other components: release date -> version (the version in use is the last one released before the campaign)
COMPONENT_RELEASES = {
    "kes":          [("2026-01-01", "3.0.0"), ("2026-05-25", "3.1.0"), ("2026-08-25", "3.2.0")],
    "ktrs":         [("2026-01-01", "5.4.2"), ("2026-06-15", "5.5.0"), ("2026-09-20", "5.5.1")],
    "gateway":      [("2026-01-01", "1.8.3"), ("2026-07-01", "2.0.0"), ("2026-07-20", "2.0.1")],
    "testcampaign": [("2026-01-01", "TC-2026.1"), ("2026-06-01", "TC-2026.2"), ("2026-09-01", "TC-2026.3")],
}

# Scripted regressions: tag -> (component, introduced in, fixed in (None = still open), failure probability,
#                               platforms affected (None = all))
REGRESSIONS = {
    "mixer_speed":       ("kms", "1.1.0", "1.2.0", 0.7, None),       # speed controller bug
    "oven_temp":         ("kms", "1.3.0", "1.4.1", 0.85, None),      # oven temperature regulation broken
    "sous_vide_timer":   ("kms", "2.0.0", "2.1.0", 0.9, None),       # timer unit bug (minutes vs hours)
    "stove_induction":   ("kms", "2.1.0", "2.1.1", 0.5, None),       # induction power curve
    "fryer_oil_temp":    ("kms", "2.2.0", None, 0.35, {"OUT"}),      # oil regulation unstable outdoors, still open
    "dishwasher_heater": ("kms", "2.4.0", None, 0.6, {"IN"}),        # new heater firmware, InHouse only
    "gateway_push":      ("gateway", "2.0.0", "2.0.1", 0.9, None),   # Gateway drops push notifications
    "grill_rotisserie":  ("kes", "3.2.0", None, 0.7, {"OUT"}),       # simulator bug: rotisserie motor on OUT
}

# Baseline random failure / environmental skip probability per action
BASE_FAIL_P = 0.004
ENV_SKIP_P = 0.002
ENV_SKIP_REASONS = [
    "Skipped by operator: ingredient not delivered",
    "Skipped by operator: module under maintenance",
    "Blocked: safety interlock open (door sensor)",
]
