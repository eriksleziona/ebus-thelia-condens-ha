BOILER_POWER_KW = 30.0
GAS_CALORIFIC_VALUE = 9.5


def delta_t(flow, ret):
    return round(flow - ret, 2)


def power_kw(mod):
    return round((mod / 100) * BOILER_POWER_KW, 2)


def gas_m3_h(power):
    return round(power / GAS_CALORIFIC_VALUE, 4)


def efficiency(delta):
    if delta < 8:
        return 108
    if delta < 15:
        return 104
    return 98
