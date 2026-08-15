from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from research_phase9.cost_engine import CostEngine

SYMBOL = 'BTCUSDT'
side = 'BUY'
entry_price = 60000.0
sl = 59000.0
tp = 62000.0
conf = 0.55

cost_engine = CostEngine.get_binance_taker_config()
p_gate = ProfitabilityGate(cost_engine)
r_gate = RiskGate(starting_balance=10000.0)

print('\n--- TESTNET EXECUTION DRY-RUN FIRST ---')
passed, p_metrics = p_gate.evaluate_signal(SYMBOL, side, entry_price, sl, tp, conf)

print(f'Signal: {side} {SYMBOL}')
print(f'Expected Gross Return: {p_metrics["expected_gross_return"]:.5f}')
print(f'Estimated Costs: {p_metrics["total_friction"]:.5f}')
print(f'Expected Net Return: {p_metrics["expected_net_return"]:.5f}')

qty = r_gate.calculate_position_size(10000.0, entry_price, sl)
print(f'Position Size: {qty}')
print(f'SL: {sl}, TP: {tp}')
print(f'Risk OK: {"YES" if qty > 0 else "NO"}')
print('--- DRY RUN COMPLETE ---\n')
