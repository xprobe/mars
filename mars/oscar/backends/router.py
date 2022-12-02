# Copyright 1999-2021 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import threading
from typing import Dict, List, Tuple, Type, Any, Optional, Union

from .communication import get_client_type, Client


class Router:
    """
    Router provides mapping from external address to internal address.
    """

    __slots__ = (
        "_curr_external_addresses",
        "_local_mapping",
        "_mapping",
        "_comm_config",
        "_cache_local",
    )

    _instance: "Router" = None

    @staticmethod
    def set_instance(router: Optional["Router"]):
        # Default router is set when an actor pool started
        Router._instance = router

    @staticmethod
    def get_instance() -> "Router":
        return Router._instance

    @staticmethod
    def get_instance_or_empty() -> "Router":
        return Router._instance or Router(list(), None)

    def __init__(
        self,
        external_addresses: List[str],
        local_address: Optional[str],
        mapping: Dict[str, str] = None,
        comm_config: dict = None,
    ):
        self._curr_external_addresses = external_addresses
        self._local_mapping = dict()
        for addr in self._curr_external_addresses:
            self._local_mapping[addr] = local_address
        if mapping is None:
            mapping = dict()
        self._mapping = mapping
        self._comm_config = comm_config or dict()
        self._cache_local = threading.local()

    @property
    def _cache(self) -> Dict[Tuple[str, Any, Optional[Type[Client]]], Client]:
        try:
            return self._cache_local.cache
        except AttributeError:
            cache = self._cache_local.cache = dict()
            return cache

    def set_mapping(self, mapping: Dict[str, str]):
        self._mapping = mapping
        self._cache_local = threading.local()

    def add_router(self, router: "Router"):
        self._curr_external_addresses.extend(router._curr_external_addresses)
        self._local_mapping.update(router._local_mapping)
        self._mapping.update(router._mapping)
        self._comm_config.update(router._comm_config)
        self._cache_local = threading.local()

    def remove_router(self, router: "Router"):
        for external_address in router._curr_external_addresses:
            try:
                self._curr_external_addresses.remove(external_address)
            except ValueError:
                pass
        for addr in router._local_mapping:
            self._local_mapping.pop(addr, None)
        for addr in router._mapping:
            self._mapping.pop(addr, None)
        self._cache_local = threading.local()

    @property
    def external_address(self):
        if self._curr_external_addresses:
            return self._curr_external_addresses[0]

    def get_internal_address(self, external_address: str) -> str:
        try:
            # local address, use dummy address
            return self._local_mapping[external_address]
        except KeyError:
            # try to lookup inner address from address mapping
            return self._mapping.get(external_address)

    async def get_client(
        self,
        external_address: str,
        from_who: Any = None,
        cached: bool = True,
        return_from_cache=False,
        **kw,
    ) -> Union[Client, Tuple[Client, bool]]:
        if cached and (external_address, from_who, None) in self._cache:
            cached_client = self._cache[external_address, from_who, None]
            if cached_client.closed:
                # closed before, ignore it
                del self._cache[external_address, from_who, None]
            else:
                if return_from_cache:
                    return cached_client, True
                else:
                    return cached_client

        address = self.get_internal_address(external_address)
        if address is None:
            # no inner address, just use external address
            address = external_address
        client_type: Type[Client] = get_client_type(address)
        client = await self._create_client(client_type, address, **kw)
        if cached:
            self._cache[external_address, from_who, None] = client
        if return_from_cache:
            return client, False
        else:
            return client

    async def _create_client(
        self, client_type: Type[Client], address: str, **kw
    ) -> Client:
        config = client_type.parse_config(self._comm_config)
        if config:
            kw["config"] = config
        local_address = (
            self._curr_external_addresses[0] if self._curr_external_addresses else None
        )
        return await client_type.connect(address, local_address=local_address, **kw)

    def _get_client_type_to_addresses(
        self, external_address: str
    ) -> Dict[Type[Client], str]:
        client_type_to_addresses = dict()
        client_type_to_addresses[get_client_type(external_address)] = external_address
        if external_address in self._curr_external_addresses:
            # local address, use dummy address
            addr = self._local_mapping.get(external_address)
            client_type = get_client_type(addr)
            client_type_to_addresses[client_type] = addr
        if external_address in self._mapping:
            # try to lookup inner address from address mapping
            addr = self._mapping.get(external_address)
            client_type = get_client_type(addr)
            client_type_to_addresses[client_type] = addr
        return client_type_to_addresses

    def get_all_client_types(self, external_address: str) -> List[Type[Client]]:
        return list(self._get_client_type_to_addresses(external_address))

    async def get_client_via_type(
        self,
        external_address: str,
        client_type: Type[Client],
        from_who: Any = None,
        cached: bool = True,
        return_from_cache=False,
        **kw,
    ) -> Union[Client, Tuple[Client, bool]]:
        if cached and (external_address, from_who, client_type) in self._cache:
            cached_client = self._cache[external_address, from_who, client_type]
            if cached_client.closed:
                # closed before, ignore it
                del self._cache[external_address, from_who, client_type]
            else:
                if return_from_cache:
                    return cached_client, True
                else:
                    return cached_client

        client_type_to_addresses = self._get_client_type_to_addresses(external_address)
        if client_type not in client_type_to_addresses:
            raise ValueError(
                f"Client type({client_type}) is not supported for {external_address}"
            )
        address = client_type_to_addresses[client_type]
        client = await self._create_client(client_type, address, **kw)
        if cached:
            self._cache[external_address, from_who, client_type] = client
        if return_from_cache:
            return client, False
        else:
            return client
