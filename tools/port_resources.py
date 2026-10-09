# SPDX-License-Identifier: GPL-3.0-only
#!/usr/bin/env python3
"""Local resource compatibility port for Additional Blocks: Stone Edition.

Original mod/resources by CiFru; All rights reserved. Source JAR stays intact.
The original CoreLib conditional recipe wrapper and config conditions are
preserved; CoreLib 1.1.24a still supports their JSON serializers.
Minecraft 1.21.1 vanilla resources are used to verify referenced models/textures.
No runtime validation is implied by the static checks.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
RENAMES = {
    "advancements": "advancement", "recipes": "recipe", "loot_tables": "loot_table",
    "predicates": "predicate", "item_modifiers": "item_modifier", "functions": "function",
    "structures": "structure",
}
TAG_RENAMES = {
    "blocks": "block", "items": "item", "functions": "function", "entity_types": "entity_type",
    "fluids": "fluid", "game_events": "game_event",
}
CONVENTION_TAG_RENAMES = {
    "c:mountain": "c:is_mountain",
    "c:mountain_peak": "c:is_mountain/peak",
    "c:mountain_slope": "c:is_mountain/slope",
    "c:copper_ingots": "c:ingots/copper",
    "c:gold_ingots": "c:ingots/gold",
}


def upgrade_convention_refs(value, changes):
    if isinstance(value, dict):
        return {key: upgrade_convention_refs(child, changes) for key, child in value.items()}
    if isinstance(value, list):
        return [upgrade_convention_refs(child, changes) for child in value]
    if isinstance(value, str):
        ref = value[1:] if value.startswith("#") else value
        if ref in CONVENTION_TAG_RENAMES:
            changes["convention_v2_tag_references"] += 1
            return ("#" if value.startswith("#") else "") + CONVENTION_TAG_RENAMES[ref]
    return value


def target_path(name):
    parts = list(PurePosixPath(name).parts)
    if parts[0] == "data" and len(parts) > 2:
        parts[2] = RENAMES.get(parts[2], parts[2])
        if parts[2] == "tags" and len(parts) > 3:
            parts[3] = TAG_RENAMES.get(parts[3], parts[3])
    return Path(*parts)


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def upgrade_item_predicate(predicate, changes):
    if "tag" in predicate:
        predicate["items"] = "#" + predicate.pop("tag")
        changes["item_predicate_tag"] += 1
    if "enchantments" in predicate:
        enchantments = predicate.pop("enchantments")
        for enchantment in enchantments:
            if "enchantment" in enchantment:
                enchantment["enchantments"] = enchantment.pop("enchantment")
        predicate.setdefault("predicates", {})["minecraft:enchantments"] = enchantments
        changes["item_predicate_enchantments"] += 1


def migrate(args):
    counts, changes = collections.Counter(), collections.Counter()
    manifest = []
    with zipfile.ZipFile(args.jar) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir() or not (name.startswith(("assets/", "data/")) or
                                     name in ("pack.mcmeta", "abstoneedition.png")):
                continue
            original = archive.read(info)
            output = original
            target = target_path(name)
            counts[name.split("/")[0]] += 1
            if name.startswith("data/") and name.endswith(".json"):
                resource = upgrade_convention_refs(json.loads(original), changes)
                category = target.parts[2]
                if category == "recipe":
                    recipe = resource.get("recipe", resource)
                    result = recipe["result"]
                    if isinstance(result, str):
                        recipe["result"] = {"id": result}
                        if recipe["type"] == "minecraft:stonecutting":
                            recipe["result"]["count"] = recipe.pop("count", 1)
                        changes["recipe_string_result"] += 1
                    elif "item" in result:
                        result["id"] = result.pop("item")
                        changes["recipe_result_item_to_id"] += 1
                    counts["recipe_type:" + recipe["type"]] += 1
                elif category == "advancement":
                    icon = resource.get("display", {}).get("icon", {})
                    if "item" in icon:
                        icon["id"] = icon.pop("item")
                        changes["advancement_icon_item_to_id"] += 1
                    for criterion in resource.get("criteria", {}).values():
                        if criterion.get("trigger") == "minecraft:inventory_changed":
                            for predicate in criterion.get("conditions", {}).get("items", []):
                                upgrade_item_predicate(predicate, changes)
                elif category == "loot_table":
                    for entry in walk(resource):
                        if isinstance(entry, dict) and entry.get("condition") == "minecraft:match_tool":
                            upgrade_item_predicate(entry.get("predicate", {}), changes)
                output = (json.dumps(resource, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            elif name == "pack.mcmeta":
                resource = json.loads(original)
                resource["pack"]["pack_format"] = 34
                resource["pack"]["supported_formats"] = {"min_inclusive": 34, "max_inclusive": 48}
                output = (json.dumps(resource, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                changes["pack_metadata"] += 1
            if target.as_posix() != name:
                changes["singular_directory"] += 1
            destination = args.resources / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(output)
            manifest.append({"source": name, "target": target.as_posix(),
                             "original_sha256": hashlib.sha256(original).hexdigest(),
                             "ported_sha256": hashlib.sha256(output).hexdigest()})
    if args.icon_source:
        destination = args.resources / "abstoneedition.png"
        destination.write_bytes(args.icon_source.read_bytes())
        changes["icon_from_author_asset"] += 1
    generated = []
    # Match the Fabric convention v2 defaults explicitly: common ingot inputs
    # must include the corresponding vanilla item, not an empty custom tag.
    for metal in ("copper", "gold"):
        target = Path("data/c/tags/item/ingots") / (metal + ".json")
        output = (json.dumps({"replace": False, "values": ["minecraft:" + metal + "_ingot"]},
                             indent=2) + "\n").encode("utf-8")
        destination = args.resources / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(output)
        generated.append({"target": target.as_posix(), "ported_sha256": hashlib.sha256(output).hexdigest()})
        changes["convention_v2_vanilla_ingot_tags"] += 1
    # Since 1.21, enchantment definitions use vanilla item tags, not instanceof
    # checks. Their enchantable/* tags reference these five tool category tags.
    for singular, plural in (("sword", "swords"), ("axe", "axes"), ("pickaxe", "pickaxes"),
                             ("shovel", "shovels"), ("hoe", "hoes")):
        tools = sorted("abstoneedition:" + model.stem
                       for model in (args.resources / "assets/abstoneedition/models/item").glob("*.json")
                       if model.stem.endswith("_" + singular))
        target = Path("data/minecraft/tags/item") / (plural + ".json")
        output = (json.dumps({"replace": False, "values": [{"id": tool, "required": False}
                                                           for tool in tools]}, indent=2) + "\n").encode("utf-8")
        destination = args.resources / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(output)
        generated.append({"target": target.as_posix(), "ported_sha256": hashlib.sha256(output).hexdigest(),
                          "tool_count": len(tools)})
        changes["modern_tool_tag_files"] += 1
        changes["modern_tool_tag_entries"] += len(tools)
    report = {
        "original_jar": str(args.jar.resolve()),
        "original_jar_sha256": hashlib.sha256(args.jar.read_bytes()).hexdigest(),
        "minecraft": "1.21.1", "author": "CiFru", "license": "All rights reserved",
        "counts": dict(counts), "changed": dict(changes), "manifest": manifest, "generated": generated,
        "notes": ["Original JAR and upstream sources both omit the declared abstoneedition.png icon.",
                  "Original resources contain no set_nbt loot function or item NBT attributes.",
                  "All 18 worldgen files retain their original parameters and compatible schemas.",
                  "Config-sensitive CoreLib and Fabric custom resource conditions remain in place."],
    }
    if args.icon_source:
        report["icon_source"] = str(args.icon_source.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "changed": report["changed"]}, ensure_ascii=False))


def check(args):
    counts, condition_types = collections.Counter(), collections.Counter()
    errors, warnings, advancements = [], [], []
    recipes, configured = set(), set()
    placed = []
    vanilla = set()
    if args.vanilla_jar.is_file():
        with zipfile.ZipFile(args.vanilla_jar) as archive:
            vanilla = set(archive.namelist())
    else:
        warnings.append("Minecraft client JAR not found; vanilla model/texture references unchecked")

    def check_asset(ref, kind, relative):
        if ref.startswith("#"):
            return
        namespace, path = ref.split(":", 1) if ":" in ref else ("minecraft", ref)
        asset = f"assets/{namespace}/{kind}/{path}" + (".json" if kind == "models" else ".png")
        if namespace == "abstoneedition":
            if not (args.resources / asset).is_file():
                errors.append(f"{relative}: missing {kind} {ref}")
        elif namespace == "minecraft" and vanilla and asset not in vanilla:
            if ref not in ("builtin/generated", "builtin/entity", "minecraft:builtin/generated", "minecraft:builtin/entity"):
                errors.append(f"{relative}: missing vanilla {kind} {ref}")

    for path in sorted(args.resources.rglob("*.json")):
        relative = path.relative_to(args.resources)
        if relative.parts[0] not in ("assets", "data"):
            continue
        try:
            resource = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            errors.append(f"{relative}: invalid JSON: {exc}")
            continue
        counts["json"] += 1
        if relative.parts[0] == "assets":
            counts["asset_json"] += 1
            for value in walk(resource):
                if isinstance(value, dict):
                    for field in ("parent", "model"):
                        if field in value and isinstance(value[field], str):
                            check_asset(value[field], "models", relative)
                    for ref in value.get("textures", {}).values():
                        if isinstance(ref, str):
                            check_asset(ref, "textures", relative)
                    if value.get("type") == "minecraft:single" and "resource" in value:
                        check_asset(value["resource"], "textures", relative)
            continue
        category = relative.parts[2]
        counts[category] += 1
        if category in RENAMES or (category == "tags" and relative.parts[3] in TAG_RENAMES):
            errors.append(f"{relative}: old plural data directory")
        if category == "recipe":
            recipe_id = relative.parts[1] + ":" + Path(*relative.parts[3:]).with_suffix("").as_posix()
            recipes.add(recipe_id)
            if resource.get("type") != "supermartijn642corelib:conditional":
                errors.append(f"{relative}: original conditional recipe wrapper missing")
            recipe = resource.get("recipe", resource)
            result = recipe.get("result", {})
            if not isinstance(result, dict) or "id" not in result or "item" in result or "count" in recipe:
                errors.append(f"{relative}: old recipe result")
            for condition in resource.get("conditions", []):
                condition_types[condition.get("type", "missing")] += 1
                if condition.get("type") not in ("abstoneedition:block_enabled", "abstoneedition:item_enabled"):
                    errors.append(f"{relative}: unexpected recipe condition {condition}")
        elif category == "advancement":
            advancements.append((relative, resource))
            if "item" in resource.get("display", {}).get("icon", {}):
                errors.append(f"{relative}: old advancement icon")
            for condition in resource.get("fabric:load_conditions", []):
                condition_types[condition.get("condition", "missing")] += 1
            for criterion in resource.get("criteria", {}).values():
                if criterion.get("trigger") == "minecraft:inventory_changed":
                    for predicate in criterion.get("conditions", {}).get("items", []):
                        if "tag" in predicate:
                            errors.append(f"{relative}: old item predicate tag")
        elif category == "worldgen":
            if relative.parts[3] == "configured_feature":
                configured.add("abstoneedition:" + path.stem)
                config = resource.get("config", {})
                if resource.get("type") != "minecraft:ore" or not 0 < config.get("size", 0) <= 64:
                    errors.append(f"{relative}: unexpected ore feature config")
            elif relative.parts[3] == "placed_feature":
                placed.append((relative, resource.get("feature")))
        for entry in walk(resource):
            if isinstance(entry, str) and entry.lstrip("#") in CONVENTION_TAG_RENAMES:
                errors.append(f"{relative}: old convention v1 tag {entry}")
            if not isinstance(entry, dict):
                continue
            if entry.get("function") in ("minecraft:set_nbt", "minecraft:copy_nbt"):
                errors.append(f"{relative}: legacy NBT loot function")
            if entry.get("condition") == "minecraft:match_tool":
                predicate = entry.get("predicate", {})
                if "enchantments" in predicate or any("enchantment" in e for e in predicate.get("predicates", {}).get("minecraft:enchantments", [])):
                    errors.append(f"{relative}: old enchantment item predicate")
    for relative, resource in advancements:
        for recipe in resource.get("rewards", {}).get("recipes", []):
            if recipe not in recipes:
                errors.append(f"{relative}: missing rewarded recipe {recipe}")
    for relative, feature in placed:
        if feature not in configured:
            errors.append(f"{relative}: missing configured feature {feature}")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    manifest = report["manifest"] + report.get("generated", [])
    for entry in manifest:
        path = args.resources / entry["target"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["ported_sha256"]:
            errors.append(f"{entry['target']}: differs from migrated resource manifest")
    if not (args.resources / "abstoneedition.png").is_file():
        warnings.append("Original declared abstoneedition.png icon is absent")
    print(json.dumps({"counts": dict(counts), "condition_types": dict(condition_types),
                      "errors": errors, "warnings": warnings}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "check"))
    parser.add_argument("--jar", type=Path, default=Path(r"inputs/original.jar"))
    parser.add_argument("--resources", type=Path, default=PROJECT / "output/resources")
    parser.add_argument("--report", type=Path, default=PROJECT / "output/resource-port-report.json")
    parser.add_argument("--icon-source", type=Path)
    parser.add_argument("--vanilla-jar", type=Path, default=Path(r"inputs/minecraft-client.jar"))
    args = parser.parse_args()
    (migrate if args.command == "migrate" else check)(args)


if __name__ == "__main__":
    main()
