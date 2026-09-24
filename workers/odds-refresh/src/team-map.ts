import rawMap from "./data/odds_team_map.json";

/** Casefold + collapse whitespace (mirrors ncaa_quant.ingestion.teams._norm_key). */
export function normKey(name: string): string {
  return name.trim().split(/\s+/).join(" ").toLowerCase();
}

const MASCOT_SUFFIX =
  /\s+(?:Falcons|Zips|Crimson Tide|Mountaineers|Sun Devils|Wildcats|Razorbacks|Red Wolves|Black Knights|Tigers|Cardinals|Broncos|Eagles|Bulls|Cougars|Golden Bears|Chippewas|49ers|Bearcats|Chanticleers|Buffaloes|Rams|Blue Devils|Pirates|Owls|Gators|Panthers|Seminoles|Bulldogs|Yellow Jackets|Rainbow Warriors|Fighting Illini|Hoosiers|Hawkeyes|Cyclones|Gamecocks|Dukes|Jayhawks|Golden Flashes|Flames|Ragin[' ]?Cajuns|Thundering Herd|Terrapins|Hurricanes|RedHawks|Spartans|Wolverines|Blue Raiders|Golden Gophers|Midshipmen|Wolfpack|Wolf Pack|Cornhuskers|Lobos|Aggies|Tar Heels|Mean Green|Huskies|Bobcats|Buckeyes|Sooners|Cowboys|Monarchs|Rebels|Ducks|Beavers|Nittany Lions|Boilermakers|Scarlet Knights|Bearkats|Aztecs|Mustangs|Jaguars|Golden Eagles|Cardinal|Orange|Horned Frogs|Volunteers|Longhorns|Red Raiders|Rockets|Trojans|Green Wave|Golden Hurricane|Blazers|Knights|Bruins|Warhawks|Minutemen|Miners|Roadrunners|Commodores|Cavaliers|Hokies|Demon Deacons|Badgers|Fighting Irish)\s*$/i;

type RawMap = Record<string, string>;

function buildLookup(raw: RawMap): Map<string, string> {
  const m = new Map<string, string>();
  for (const [k, v] of Object.entries(raw)) {
    m.set(normKey(k), v.trim());
  }
  return m;
}

const LOOKUP = buildLookup(rawMap as RawMap);

/**
 * Map Odds API / sportsbook display name → CFBD-aligned school name.
 * Exact map lookup only after casefold; optional deterministic mascot strip.
 * Never fuzzy-matches.
 */
export function normalizeTeamName(name: string, lookup: Map<string, string> = LOOKUP): string {
  const cleaned = name.trim().split(/\s+/).join(" ");
  if (!cleaned) return cleaned;
  const hit = lookup.get(normKey(cleaned));
  if (hit !== undefined) return hit;
  const stripped = cleaned.replace(MASCOT_SUFFIX, "").trim();
  if (stripped && stripped !== cleaned) {
    const hit2 = lookup.get(normKey(stripped));
    if (hit2 !== undefined) return hit2;
    return stripped;
  }
  return cleaned;
}

export function teamMapSize(): number {
  return LOOKUP.size;
}

export { LOOKUP as TEAM_LOOKUP };
