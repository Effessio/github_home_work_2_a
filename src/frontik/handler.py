# -*- coding: utf-8 -*-

from __future__ import with_statement

import os.path
import traceback

from functools import partial

import tornado.web
import tornado.httpclient
import tornado.options

from frontik import etree
from frontik.doc import Doc

import logging
log = logging.getLogger('frontik.handler')

import future
http_client = tornado.httpclient.AsyncHTTPClient(max_clients=200, max_simultaneous_connections=200)

def http_header_out(*args, **kwargs):
    pass

def set_http_status(*args, **kwargs):
    pass

# TODO cleanup this
ns = etree.FunctionNamespace('http://www.yandex.ru/xscript')
ns.prefix = 'x'
ns['http-header-out'] = http_header_out
ns['set-http-status'] = set_http_status 

class ResponsePlaceholder(future.FutureVal):
    def __init__(self):
        pass

    def set_response(self, handler, response):
        self.response = response

        if response.error:
            handler.log.warn('%s failed %s', response.code, response.effective_url)

    def get(self):
        if not self.response.error:
            try:
                return [etree.Comment(self.response.effective_url), etree.fromstring(self.response.body)]
            except:
                return etree.Element('error', dict(url=response.effective_url, reason='invalid XML'))
        else:
            return etree.Element('error', dict(url=response.effective_url, reason=self.response.error.message))

class Stats:
    def __init__(self):
        self.page_count = 0
        self.http_reqs_count = 0

stats = Stats()

class PageHandler(tornado.web.RequestHandler):
    def __init__(self, *args, **kw):
        tornado.web.RequestHandler.__init__(self, *args, **kw)
        
        self.doc = Doc()
        self.n_waiting_reqs = 0
        self.finishing = False
        self.transform = None
        
        self.request_id = self.request.headers.get('X-Request-Id', self.get_next_request_id())
        
        self.log = logging.getLogger('frontik.handler.%s' % (self.request_id,))
        
        self.log.debug('started %s %s', self.request.method, self.request.uri)

    
    @classmethod
    def get_next_request_id(cls):
        stats.page_count += 1
        return stats.page_count
    
    def fetch_url(self, url):
        placeholder = ResponsePlaceholder()
        self.n_waiting_reqs += 1
        stats.http_reqs_count += 1
        
        http_client.fetch(
            tornado.httpclient.HTTPRequest(
                url=url,
                headers={
                    'Connection':'Keep-Alive',
                    'Keep-Alive':'1000'}), 
            self.async_callback(partial(self._fetch_url_response, placeholder)))
        
        return placeholder
        
    def _fetch_url_response(self, placeholder, response):
        self.n_waiting_reqs -= 1
        self.log.debug('got %s %s in %.3f, %s requests pending', response.code, response.effective_url, response.request_time, self.n_waiting_reqs)
        
        placeholder.set_response(self, response)
        self._try_finish_page()

    def finish_page(self):
        self.log.debug('going to finish')
        
        self.finishing = True
        self._try_finish_page()
    
    def _try_finish_page(self):
        if self.finishing and self.n_waiting_reqs == 0:
            if (self.transform):
                self._real_finish_with_xsl()
            else:
                self._real_finish()

    def _real_finish_with_xsl(self):
        self.log.debug('finishing')
        self.set_header('Content-Type', 'text/html')

        try:
            result = str(self.transform(self.doc.to_etree_element()))
            self.log.debug('applying XSLT %s', self.transform_filename)

        except:
            result = ""
            self.log.error('failed transformation with XSL %s' % self.transform_filename)


        self.write(result)
        self.log.debug('done')
        self.finish('')

    
    def _real_finish(self):
        self.log.debug('finishing')

        self.set_header('Content-Type', 'application/xml')

        self.write(self.doc.to_string())

        self.log.debug('done')

        self.finish('')

    ###
    xml_files_cache = dict()

    def xml_from_file(self, filename):
        if filename in self.xml_files_cache:
            self.log.debug('got %s file from cache', filename)
            return self.xml_files_cache[filename]
        else:
            ret = self._xml_from_file(filename)
            self.xml_files_cache[filename] = ret
            return [etree.Comment('file: %s' % (filename,)),
                    ret]

    def _xml_from_file(self, filename):
        real_filename = os.path.join(self.request.config.XML_root, filename)
        self.log.debug('read %s file from %s', filename, real_filename)

        if os.path.exists(real_filename):
            try:
                return etree.parse(file(real_filename)).getroot()
            except:
                return etree.Element('error', dict(msg='failed to parse file: %s' % (filename,)))
        else:
            return etree.Element('error', dict(msg='file not found: %s' % (filename,)))

    ###
    xsl_files_cache = dict()

    def set_xsl(self, filename):
        if self.get_argument('noxsl', None):
            return
        real_filename = os.path.join(self.request.config.XSL_root, filename)

        def gen_transformation():
            tree = etree.parse(fp)
            self.log.debug('parsed XSL file %s', real_filename)
            transform = etree.XSLT(tree)
            self.log.debug('generated transformation from XSL file %s', real_filename)
            return transform

        with open(real_filename, "rb") as fp:
            self.log.debug('read file %s', real_filename)
            try:
                if self.xsl_files_cache.has_key(real_filename):
                    self.transform = self.xsl_files_cache[real_filename]
                else:
                    tree = etree.parse(fp)
                    self.transform = etree.XSLT(tree)
                    self.xsl_files_cache[real_filename] = self.transform
            except etree.XMLSyntaxError, error:
                self.log.exception('failed parsing XSL file %s' % real_filename)
            except:
                self.log.exception('XSL transformation error with file %s' % real_filename)
            self.transform_filename = real_filename
