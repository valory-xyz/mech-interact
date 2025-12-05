# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""Mechs' info queries."""


from string import Template


info = Template(
    """
        {
            meches(
                first: ${first},
                orderBy: id,
                orderDirection: asc,
                where: {
                    id_gt: "${mechs_id_gt}",
                    service_: {totalDeliveries_gt: 0},
                    address_not_in: ["${ignored_mechs}"]
                }
            ) {
                id
                address
                maxDeliveryRate
                karma
                receivedRequests
                selfDeliveredFromReceived
                service {
                    metadata {
                        metadata
                    }
                    deliveries(
                        first: 1,
                        orderBy: blockTimestamp,
                        orderDirection: desc
                    ) {
                        blockTimestamp
                    }
                }
            }
        }
    """
)
