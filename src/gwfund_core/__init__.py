"""gwfund-core 鈥斺€?娓愭湪鍏嫙鍩洪噾鐮旂┒绯荤粺 路 鏍稿績绠楁硶搴撱€?
**鏈寘鏄洓绔紙妗岄潰 / 缃戠珯 / 灏忕▼搴?/ API锛夌殑鍞竴绠楁硶鐪熺浉婧?*锛圫OW 搂7.1銆侀檮浠禖 MC-01锛夈€?浠讳綍绔笉寰楀鍒舵湰鍖呭唴鐨勫叕寮忥紱鍥涚涓€鑷存€ч潬銆岀増鏈攣 + 榛勯噾鍚戦噺 + 姣忔棩姣斿銆嶄繚璇併€?
璁捐绾︽潫锛圫OW 搂7.3 C3/C10銆侀檮浠禔 搂10锛夛細

* **绾嚱鏁?*锛氭棤 I/O銆佹棤缃戠粶銆佹棤鏂囦欢璇诲啓銆佹棤鍏ㄥ眬鍙彉鐘舵€侊紱
* **闆惰繍琛屾椂渚濊禆**锛氬彧鐢?Python 鏍囧噯搴擄紙``decimal`` / ``math`` / ``dataclasses``锛夛紱
* **缂哄け鍗?None**锛氱己澶卞€间竴寰?``None``锛岀姝㈠啓 ``0``銆佺姝㈠墠鍚戝～鍏咃紙闄勪欢A 搂2.2锛夛紱
* **瓒呴槇鍊兼姏寮傚父**锛氭亽绛夊紡涓庡綊鍥犳畫宸秴闃堝€兼椂鎶涘紓甯革紝涓嶉潤榛橈紙闄勪欢A 搂10 E7锛夈€?
鍏稿瀷鐢ㄦ硶::

    from gwfund_core import returns, fees, identities, attribution

    # TWR锛氭棩棰?r_t 閾惧紡杩炰箻
    rs = [returns.daily_return(a, b) for a, b in zip(navs[:-1], navs[1:])]
    twr = returns.chain_return(rs)

    # MWR锛氱幇閲戞祦姹?IRR锛堟姇璧勪负璐熴€佹敹鍥炰负姝ｏ級
    res = returns.irr([-1_000_000, -500_000, 0, 1_520_000])

    # 鍩烘皯鏀剁泭缂哄彛
    gap = returns.investor_gap(twr, res.value)

    # 涓夋潯鎭掔瓑寮?    check = identities.check_identity_a(a_start=..., a_end=..., r_net=...,
                                        net_sub=..., div_cash=...)

鍏嶈矗澹版槑锛氭湰搴撲负鐮旂┒宸ュ叿鐨勮绠楀唴鏍革紝鎵€鏈夋祴绠楀熀浜庡叕寮€鎶湶鏁版嵁涓庡巻鍙插尯闂淬€?**鍘嗗彶娴嬬畻缁撴灉涓嶇瓑浜庢湭鏉ヨ〃鐜帮紝涓嶆瀯鎴愭姇璧勫缓璁€?*
"""

from __future__ import annotations

from . import attribution, chase, fees, identities, mixing, returns
from .errors import (
    AttributionResidualError,
    GwFundError,
    GwFundWarning,
    IdentityResidualError,
    InsufficientDataError,
    MwrMultiRootWarning,
    NetSubMethodWarning,
    NoIrrSolutionError,
    OtherResidualWarning,
    PrecisionLevelViolationError,
)
from .types import (
    FeeAccrual,
    FeeRate,
    IdentityCheck,
    MixingMethod,
    MwrResult,
    NavPoint,
    PrecisionLevel,
    SharePeriod,
    WaterfallResult,
    WaterfallSegment,
    WeightScheme,
)

__version__ = "0.2.0"
__all__ = [
    "__version__",
    # 瀛愭ā鍧?    "attribution",
    "chase",
    "fees",
    "identities",
    "mixing",
    "returns",
    # 绫诲瀷
    "FeeAccrual",
    "FeeRate",
    "IdentityCheck",
    "MixingMethod",
    "MwrResult",
    "NavPoint",
    "PrecisionLevel",
    "SharePeriod",
    "WaterfallResult",
    "WaterfallSegment",
    "WeightScheme",
    # 寮傚父涓庤鍛?    "AttributionResidualError",
    "GwFundError",
    "GwFundWarning",
    "IdentityResidualError",
    "InsufficientDataError",
    "MwrMultiRootWarning",
    "NetSubMethodWarning",
    "NoIrrSolutionError",
    "OtherResidualWarning",
    "PrecisionLevelViolationError",
]

#: 绠楁硶鍙ｅ緞鎵€渚濇嵁鐨勮鑼冪増鏈紙鍐欒繘 API 鍝嶅簲 meta锛屼究浜庡鐜帮級
SPEC_VERSION = "闄勪欢A v1.0 / SOW v1.1"
