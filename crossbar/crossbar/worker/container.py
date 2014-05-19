###############################################################################
##
##  Copyright (C) 2014 Tavendo GmbH
##
##  This program is free software: you can redistribute it and/or modify
##  it under the terms of the GNU Affero General Public License, version 3,
##  as published by the Free Software Foundation.
##
##  This program is distributed in the hope that it will be useful,
##  but WITHOUT ANY WARRANTY; without even the implied warranty of
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
##  GNU Affero General Public License for more details.
##
##  You should have received a copy of the GNU Affero General Public License
##  along with this program. If not, see <http://www.gnu.org/licenses/>.
##
###############################################################################

from __future__ import absolute_import

__all__ = ['ContainerWorker']


import os

from twisted.internet.defer import DeferredList, inlineCallbacks

from autobahn.wamp.exception import ApplicationError
from autobahn.wamp.types import ComponentConfig

from twisted.python import log
import pkg_resources


from crossbar.worker.native import NativeWorker




class ContainerWorker(NativeWorker):
   """
   A container worker hosts application components written in Python, and
   connects to an application router.
   """
   WORKER_TYPE = 'container'


   @inlineCallbacks
   def onJoin(self, details):
      """
      Called when worker process has joined the node's management realm.
      """
      dl = []
      procs = [
         'start_component',
      ]

      for proc in procs:
         uri = 'crossbar.node.{}.worker.{}.container.{}'.format(self.config.extra.node, self.config.extra.pid, proc)
         dl.append(self.register(getattr(self, proc), uri))

      regs = yield DeferredList(dl)

      yield NativeWorker.onJoin(self, details)


   def start_component(self, component, router):
      """
      Starts a Class or WAMPlet in this component container.
      """
      ## create component
      ##
      if component['type'] == 'wamplet':

         try:
            dist = component['dist']
            name = component['entry']

            if self.debug:
               log.msg("Starting WAMPlet '{}/{}' in realm '{}' ..".format(dist, name, router['realm']))

            ## make is supposed to make instances of ApplicationSession
            make = pkg_resources.load_entry_point(dist, 'autobahn.twisted.wamplet', name)

         except Exception as e:
            log.msg("Failed to import class - {}".format(e))
            raise ApplicationError("crossbar.error.class_import_failed", str(e))

      elif component['type'] == 'class':

         try:
            klassname = component['name']

            if self.debug:
               log.msg("Worker {}: starting class '{}' in realm '{}' ..".format(self.config.extra.pid, klassname, router['realm']))

            import importlib
            c = klassname.split('.')
            mod, kls = '.'.join(c[:-1]), c[-1]
            app = importlib.import_module(mod)

            ## make is supposed to be of class ApplicationSession
            make = getattr(app, kls)

         except Exception as e:
            log.msg("Worker {}: failed to import class - {}".format(e))
            raise ApplicationError("crossbar.error.class_import_failed", str(e))

      else:
         raise ApplicationError("crossbar.error.invalid_configuration", "unknown component type '{}'".format(component['type']))


      def create():
         cfg = ComponentConfig(realm = router['realm'], extra = component.get('extra', None))
         c = make(cfg)
         return c


      ## create the WAMP transport
      ##
      transport_config = router['transport']
      transport_debug = transport_config.get('debug', False)

      if transport_config['type'] == 'websocket':

         ## create a WAMP-over-WebSocket transport client factory
         ##
         #from autobahn.twisted.websocket import WampWebSocketClientFactory
         #transport_factory = WampWebSocketClientFactory(create, transport_config['url'], debug = transport_debug, debug_wamp = transport_debug)
         from crossbar.router.protocol import CrossbarWampWebSocketClientFactory
         transport_factory = CrossbarWampWebSocketClientFactory(create, transport_config['url'], debug = transport_debug, debug_wamp = transport_debug)
         transport_factory.setProtocolOptions(failByDrop = False)

      elif transport_config['type'] == 'rawsocket':

         from crossbar.router.protocol import CrossbarWampRawSocketClientFactory
         transport_factory = CrossbarWampRawSocketClientFactory(create, transport_config)

      else:
         raise ApplicationError("crossbar.error.invalid_configuration", "unknown transport type '{}'".format(transport_config['type']))


      self._foo = transport_factory

      ## create client endpoint
      ##
      from twisted.internet import reactor
      from crossbar.twisted.endpoint import create_connecting_endpoint_from_config

      endpoint = create_connecting_endpoint_from_config(transport_config['endpoint'], self.config.extra.cbdir, reactor)

      ## now connect the client
      ##
      retry = True
      retryDelay = 1000

      def try_connect():
         if self.debug:
            log.msg("Connecting to application router ..")

         d = endpoint.connect(transport_factory)

         def success(proto):
            if self.debug:
               log.msg("Connected to application router")

         def error(err):
            log.msg("Failed to connect to application router: {}".format(err))
            if retry:
               log.msg("Retrying to connect in {} ms".format(retryDelay))
               reactor.callLater(float(retryDelay) / 1000., try_connect)
            else:
               log.msg("Could not connect to application router - giving up.")

         d.addCallbacks(success, error)

      try_connect()
