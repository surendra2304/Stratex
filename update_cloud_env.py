from dotenv import set_key
p = '/opt/friday_universe/Stratex/.env'
set_key(p, 'CTRADER_FIX_ENABLED', 'true')
set_key(p, 'CTRADER_FIX_HOST', 'live.cfixapi.com')
set_key(p, 'CTRADER_FIX_PORT', '5212')
set_key(p, 'CTRADER_FIX_SENDER_COMP_ID', 'live4.icmarkets.6188250')
set_key(p, 'CTRADER_FIX_TARGET_COMP_ID', 'CSERVER')
set_key(p, 'CTRADER_FIX_ACCOUNT_ID', '6188250')
set_key(p, 'CTRADER_FIX_PASSWORD', 'stratex')
print('ENV UPDATED SUCCESSFULLY')
