import re

with open('/root/.openclaw/workspace/projects/icechaser/backend/generate_data.py', 'r') as f:
    content = f.read()

old = '''    best_worst_cases = {}
    try:
        brute_best_worst, brute_scenarios = run_brute_force_scenarios(teams, today_games, baseline_results=sim_results)
        team_scenarios = brute_scenarios
        for abbrev, data in brute_best_worst.items():
            best_worst_cases[abbrev] = {
                "best": data["best"],
                "medium": round((data["best"] + data["worst"]) / 2, 1),
                "worst": data["worst"],
                "has_game": data["has_game"],
            }
        print(f"   ✓ Unified brute-force complete")
    except Exception as e:
        print(f"   ✗ Brute-force error: {e}")
        import traceback
        traceback.print_exc()
        team_scenarios = {}
        best_worst_cases = {}'''

new = '''    best_worst_cases = {}
    try:
        brute_best_worst, brute_scenarios = run_brute_force_scenarios(teams, today_games, baseline_results=sim_results)
        scenario_results = simulator.run_scenario_analysis_vectorized(
            teams, today_games, num_simulations=NUM_SIMULATIONS, real_schedule=real_schedule
        )
        scenario_best_worst, team_scenarios, _, _, _ = scenario_results
        for abbrev, data in scenario_best_worst.items():
            best_worst_cases[abbrev] = {
                "best": data["best"],
                "medium": round((data["best"] + data["worst"]) / 2, 1),
                "worst": data["worst"],
                "has_game": data["has_game"],
            }
        print(f"   ✓ Unified brute-force complete")
    except Exception as e:
        print(f"   ✗ Brute-force error: {e}")
        import traceback
        traceback.print_exc()
        team_scenarios = {}
        best_worst_cases = {}'''

if old in content:
    content = content.replace(old, new)
    with open('/root/.openclaw/workspace/projects/icechaser/backend/generate_data.py', 'w') as f:
        f.write(content)
    print("SUCCESS")
else:
    print("ERROR: block not found")
