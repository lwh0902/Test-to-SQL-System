from app.application.contracts import KERNEL_V2
from app.application.kernel_route import resolve_kernel_route
from app.pilot.flags import PilotFlags


def test_kernel_route_is_v2_even_when_historical_flag_is_disabled():
    assert resolve_kernel_route(PilotFlags(analysis_kernel=False)) == KERNEL_V2
