# -*-*- encoding: utf-8 -*-*-

import os
import re
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback
import pprint

import requests
from bs4 import BeautifulSoup

from flask import Blueprint, request, url_for
from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError, register_blueprint
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions
from labmanager.rlms.queue import QueueTask, run_tasks

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class VirtualBiologyLabAddForm(AddForm):

    DEFAULT_URL = 'http://www.virtualbiologylab.org'
    DEFAULT_LOCATION = 'United States'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'virtualbiologylab'
    DEFAULT_AUTOLOAD = True

    def __init__(self, add_or_edit, *args, **kwargs):
        super(VirtualBiologyLabAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        return new_configuration


class VirtualBiologyLabFormCreator(BaseFormCreator):

    def get_add_form(self):
        return VirtualBiologyLabAddForm

MIN_TIME = datetime.timedelta(hours=24)

def get_laboratories():
    labs_and_identifiers  = VIRTUALBIOLOGYLAB.rlms_cache.get('get_laboratories',  min_time = MIN_TIME)
    if labs_and_identifiers:
        labs, identifiers = labs_and_identifiers
        return labs, identifiers

    index = requests.get('http://virtualbiologylab.org/site-map/').text
    soup = BeautifulSoup(index, 'lxml')

    identifiers = {
        # identifier: {
        #     'name': name,
        #     'link': link,
        # }
    }

    identifiers['ModelsHTML5_IslandBiogeography_IslandBiogeography'] = {
        'name': 'IslandBiogeography',
        'link': 'http://virtualbiologylab.org/ModelsHTML5/IslandBiogeography/IslandBiogeography.html',
    }

    for anchor_link in soup.find_all('a'):
        if ' html ' in anchor_link.text.lower():
            href = anchor_link['href']
            identifier = create_identifier(href)
            identifiers[identifier] = {
                'name': anchor_link.parent.find("strong").text.splitlines()[0].strip(),
                'link': href,
            }

    model_links = soup.find_all(class_='menu-text', text=re.compile('.*[mM]odel.*'))
    menu_links = []
    for model_link in model_links:
        for menu_link in model_link.parent.find_next('ul').find_all('a'):
            menu_links.append(menu_link)

    for link in menu_links: # they're few: 5-10
        html = requests.get(link['href']).text
        soup_link = BeautifulSoup(html, 'lxml')
        for launch_model in soup_link.find_all(text=re.compile('.*launch model.*', flags=re.IGNORECASE)):
            final_link = launch_model.find_parent('a')
            if final_link:
                href = final_link['href']
                name = href.rsplit('/')[-1].split('.')[0]

                # If possible, take the name
                column_wrapper = final_link.find_parent(class_='fusion-column-wrapper')
                if column_wrapper:
                    model_name = column_wrapper.find('strong')
                    if model_name and u'\u2013' in model_name:
                        name = model_name.split('\u2013', 1)[1].strip()
                    elif model_name and u'-' in model_name:
                        name = model_name.split('-', 1)[1].strip()

                identifier = create_identifier(href)
                if identifier not in identifiers:
                    identifiers[identifier] = {
                        'name': name,
                        'link': href,
                    }



    labs = []
    for identifier, identifier_data in identifiers.items():
        name = identifier_data['name']
        lab = Laboratory(name=name, laboratory_id=identifier, description=name)
        labs.append(lab)

    VIRTUALBIOLOGYLAB.rlms_cache['get_laboratories'] = (labs, identifiers)
    return labs, identifiers

def create_identifier(url):
    path = urlparse.urlparse(url).path
    identifier = path[1:].replace('/', '_').replace('.html', '')
    return identifier

FORM_CREATOR = VirtualBiologyLabFormCreator()

CAPABILITIES = [ Capabilities.WIDGET, Capabilities.URL_FINDER, Capabilities.CHECK_URLS ]

class RLMS(BaseRLMS):

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return CAPABILITIES

    def get_laboratories(self, **kwargs):
        labs, identifiers = get_laboratories()
        return labs

    def get_base_urls(self):
        return [ 'https://virtualbiologylab.org', 'http://virtualbiologylab.org', 'https://www.virtualbiologylab.org', 'http://www.virtualbiologylab.org' ]

    def get_lab_by_url(self, url):
        identifier = create_identifier(url)

        laboratories, identifiers = get_laboratories()
        for lab in laboratories:
            if lab.laboratory_id == identifier:
                return lab

        return None

    def get_check_urls(self, laboratory_id):
        laboratories, identifiers = get_laboratories()
        lab_data = identifiers.get(laboratory_id)
        if lab_data:
            return [ lab_data['link'] ]
        return []

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        laboratories, identifiers = get_laboratories()
        if laboratory_id not in identifiers:
            raise LabNotFoundError("Laboratory not found: {}".format(laboratory_id))

        url = identifiers[laboratory_id]['link']
        if url.startswith('http://'):
            url = 'https://gateway.golabz.eu/proxy/{}'.format(url)

        response = {
            'reservation_id' : url,
            'load_url' : url,
        }
        return response


    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]


class VirtualBiologyLabTaskQueue(QueueTask):
    RLMS_CLASS = RLMS

def populate_cache(rlms):
    rlms.get_laboratories()

VIRTUALBIOLOGYLAB = register("VirtualBiologyLab", ['1.0'], __name__)
VIRTUALBIOLOGYLAB.add_local_periodic_task('Populating cache', populate_cache, hours = 23)

DEBUG = VIRTUALBIOLOGYLAB.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

if __name__ == '__main__':
    rlms = RLMS('{}')
    labs = rlms.get_laboratories()
    for lab in labs:
        print rlms.reserve(lab.laboratory_id, 'nobody', 'nowhere', '{}', [], {}, {})
