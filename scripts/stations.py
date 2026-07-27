"""Rail stations for the greater-Copenhagen / North Zealand corridor.

Coordinates come from OpenStreetMap (railway=station/halt, matched by name via
Overpass) so they land on the actual platforms on the tile basemap. Each station has a
corridor label and a `strain` flag (True = S-train, False = Kystbanen regional,
which is what serves the coastal Hørsholm/Fredensborg towns that have no S-train).
"""

# name, corridor, lat, lon, is_strain
STATIONS = [
    # --- Central trunk (shared) ---
    ("København H", "central", 55.67219, 12.56461, True),
    ("Vesterport", "central", 55.67582, 12.56241, True),
    ("Nørreport", "central", 55.68343, 12.57177, True),
    ("Østerport", "central", 55.69288, 12.58592, True),
    ("Nordhavn", "central", 55.70497, 12.59101, True),
    ("Svanemøllen", "central", 55.71553, 12.57883, True),
    ("Hellerup", "central", 55.73059, 12.56691, True),

    # --- Hillerød line (A) north of Hellerup ---
    ("Bernstorffsvej", "hilleroed", 55.74281, 12.55781, True),
    ("Gentofte", "hilleroed", 55.75359, 12.54144, True),
    ("Jægersborg", "hilleroed", 55.76174, 12.52123, True),
    ("Lyngby", "hilleroed", 55.76806, 12.50311, True),
    ("Sorgenfri", "hilleroed", 55.78121, 12.48359, True),
    ("Virum", "hilleroed", 55.79615, 12.47298, True),
    ("Holte", "hilleroed", 55.80769, 12.46853, True),
    ("Birkerød", "hilleroed", 55.84038, 12.4235, True),
    ("Allerød", "hilleroed", 55.87109, 12.35695, True),
    ("Hillerød", "hilleroed", 55.92767, 12.31139, True),

    # --- Klampenborg line (C) north of Hellerup ---
    ("Charlottenlund", "klampenborg", 55.75177, 12.57235, True),
    ("Ordrup", "klampenborg", 55.7629, 12.58356, True),
    ("Klampenborg", "klampenborg", 55.77681, 12.58863, True),

    # --- Farum line (B) via Gladsaxe / Furesø ---
    ("Buddinge", "farum", 55.74716, 12.49418, True),
    ("Stengården", "farum", 55.75672, 12.4731, True),
    ("Bagsværd", "farum", 55.76159, 12.45456, True),
    ("Skovbrynet", "farum", 55.76516, 12.43373, True),
    ("Hareskov", "farum", 55.76522, 12.40797, True),
    ("Værløse", "farum", 55.78245, 12.37192, True),
    ("Farum", "farum", 55.81211, 12.37371, True),

    # --- Frederikssund line (H) via Herlev / Ballerup / Egedal ---
    ("Herlev", "frederikssund", 55.71884, 12.44355, True),
    ("Skovlunde", "frederikssund", 55.72306, 12.40271, True),
    ("Ballerup", "frederikssund", 55.72972, 12.35829, True),
    ("Malmparken", "frederikssund", 55.72466, 12.3857, True),
    ("Måløv", "frederikssund", 55.74739, 12.31813, True),
    ("Kildedal", "frederikssund", 55.75196, 12.28646, True),
    ("Veksø", "frederikssund", 55.74992, 12.24059, True),
    ("Ølstykke", "frederikssund", 55.79577, 12.15932, True),

    # --- Kystbanen (regional, NOT S-train) — Hørsholm / Fredensborg coast ---
    ("Skodsborg", "kystbanen", 55.82349, 12.57168, False),
    ("Vedbæk", "kystbanen", 55.85273, 12.56245, False),
    ("Rungsted Kyst", "kystbanen", 55.88244, 12.53151, False),
    ("Kokkedal", "kystbanen", 55.90326, 12.50242, False),
    ("Nivå", "kystbanen", 55.93343, 12.50625, False),
    ("Humlebæk", "kystbanen", 55.96389, 12.53306, False),
]

# Order of stations along each corridor, for drawing schematic polylines.
LINES = {
    "central":       ["København H", "Vesterport", "Nørreport", "Østerport",
                      "Nordhavn", "Svanemøllen", "Hellerup"],
    "hilleroed":     ["Hellerup", "Bernstorffsvej", "Gentofte", "Jægersborg",
                      "Lyngby", "Sorgenfri", "Virum", "Holte", "Birkerød",
                      "Allerød", "Hillerød"],
    "klampenborg":   ["Hellerup", "Charlottenlund", "Ordrup", "Klampenborg"],
    "farum":         ["København H", "Buddinge", "Stengården", "Bagsværd",
                      "Skovbrynet", "Hareskov", "Værløse", "Farum"],
    "frederikssund": ["København H", "Herlev", "Skovlunde", "Malmparken",
                      "Ballerup", "Måløv", "Kildedal", "Veksø", "Ølstykke"],
    "kystbanen":     ["Klampenborg", "Skodsborg", "Vedbæk", "Rungsted Kyst",
                      "Kokkedal", "Nivå", "Humlebæk"],
}

LINE_LABELS = {
    "central":       "S-tog (city)",
    "hilleroed":     "S-tog · Hillerød (A)",
    "klampenborg":   "S-tog · Klampenborg (C)",
    "farum":         "S-tog · Farum (B)",
    "frederikssund": "S-tog · Frederikssund (H)",
    "kystbanen":     "Kystbanen (regional)",
}
