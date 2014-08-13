# encoding: utf-8
"""
process.py

Created by Thomas Mangin on 2011-11-29.
Copyright (c) 2011-2013  Exa Networks. All rights reserved.
"""

from .response import ResponseEncoder as Respond
from exaproxy.icap.parser import ICAPParser

from .worker import Redirector


class ICAPRedirector (Redirector):
	ICAPParser = ICAPParser

	def __init__ (self, configuration, name, program, protocol):
		self.icap_parser = self.ICAPParser(configuration)
		self.protocol = protocol
		self.icap = protocol[len('icap://'):].split('/')[0]

		Redirector.__init__ (self, configuration, name, program, protocol)

	def readChildResponse (self):
		try:
			response = self.process.stdout.readline()
			code = (response.rstrip().split()+[None])[1] if response else None
			length = -1

			while True:
				line = self.process.stdout.readline()
				response += line

				if not line:
					response = None
					break

				elif not line.rstrip():
					break

				if line.startswith('Encapsulated: res-hdr=0, null-body='):
					length = int(line.split('=')[-1])

			read_bytes = 0
			bytes_to_read = max(0, length)

			while read_bytes < bytes_to_read:
				headers_s = self.process.stdout.read(bytes_to_read-read_bytes)
				response += headers_s
				read_bytes += len(headers_s)

			if code is None:
				response = None

			# 304 (not modified)
			elif code != '304' and length < 0:
				response = None

		except IOError:
			response = None

		try:
			child_stderr = self.process.stderr.read(4096)
		except Exception, e:
			child_stderr = ''

		if child_stderr:
			response = None

		return response


	def createChildRequest (self, peer, message, http_header):
		return self.createICAPRequest(peer, message, None, http_header)

	def createICAPRequest (self, peer, message, icap_message, http_header):
		username = icap_message.headers.get('x-authenticated-user', '').strip() if icap_message else None
		groups = icap_message.headers.get('x-authenticated-groups', '').strip() if icap_message else None
		ip_addr = icap_message.headers.get('x-client-ip', '').strip() if icap_message else None
		customer = icap_message.headers.get('x-customer-name', '').strip() if icap_message else None

		icap_request = """\
REQMOD %s ICAP/1.0
Host: %s
Pragma: client=%s
Pragma: host=%s""" % (
			self.protocol, self.icap,
			peer, message.host,
			)

		if ip_addr:
			icap_request += """
X-Client-IP: %s""" % ip_addr

		if username:
			icap_request += """
X-Authenticated-User: %s""" % username

		if groups:
			icap_request += """
X-Authenticated-Groups: %s""" % groups

		if customer:
			icap_request += """
X-Customer-Name: %s""" % customer

		return icap_request + """
Encapsulated: req-hdr=0, null-body=%d

%s""" % (len(http_header), http_header)



	def decideICAP (self, client_id, icap_response):
		return Respond.icap(client_id, icap_response) if icap_response else None

	def decideHTTP (self, client_id, icap_response, message, peer, source):
		# 304 (not modified)
		if icap_response.is_permit:
			classification, data, comment = 'permit', None, None

		elif icap_response.is_modify:
			message = self.parseHTTP(client_id, peer, icap_response.http_header)
			if message.validated:
				classification, data, comment = 'permit', None, None

			else:
				classification, data, comment = None, None, None

		elif icap_response.is_content:
			classification, data, comment = 'http', icap_response.http_header, icap_response.pragma.get('comment', '')

		elif icap_response.is_intercept:
			classification, data, comment = 'intercept', icap_response.destination, icap_response.pragma.get('comment', '')

		else:
			classification, data, comment = 'permit', None, None

		if classification is None:
			response = self.validateHTTP(client_id, message)
			if response:
				classification, data, comment = response

			else:
				classification, data, comment = 'error', None, None

		if classification == 'requeue':
			(operation, destination) = None, None
			decision = Respond.requeue(client_id, peer, header, subheader, source)

		elif message.request.method in ('GET','PUT','POST','HEAD','DELETE','PATCH'):
			(operation, destination), decision = self.response_factory.contentResponse(client_id, message, classification, data, comment)

		elif message.request.method == 'CONNECT':
			(operation, destination), decision = self.response_factory.connectResponse(client_id, message, classification, data, comment)

		else:
			# How did we get here
			operation, destination, decision = None, None, None

		return decision


	def doICAP (self, client_id, peer, icap_header, http_header, tainted):
		icap_request = self.icap_parser.parseRequest(peer, icap_header, http_header)
		http_request = self.http_parser.parseRequest(peer, http_header)

		request_string = self.createICAPRequest(peer, http_request, icap_request, http_header) if icap_request else None
		return self.queryChild(request_string) if request_string else None

	def decide (self, client_id, peer, header, subheader, source):
		if self.checkChild():
			if source == 'icap':
				response = self.doICAP(client_id, peer, header, subheader)

			elif source == 'proxy':
				response = self.doHTTP(client_id, peer, header, source)

			elif source == 'web':
				response = self.doMonitor(client_id, peer, header, source)

			else:
				response = Respond.hangup(client_id)

		else:
			response = Respond.error(client_id)

		return response

	def progress (self, client_id, peer, message, http_header, subheader, source):
		if self.checkChild():
			response_string = self.readChildResponse()

		else:
			response_string = None

		if response_string is not None and source == 'icap':
			decision = self.decideICAP(client_id, response_string)

		elif response_string is not None and source == 'proxy':
			icap_header, http_header = self.icap_parser.splitResponse(response_string)
			icap_response = self.icap_parser.parseResponse(icap_header, http_header)
			decision = self.decideHTTP(client_id, icap_response, message, peer, source)

		elif response_string is not None:
			decision = Respond.hangup(client_id)

		else:
			decision = Respond.error(client_id)

		return decision
