#!/usr/bin/python3
# heat-ai - Machine learning based whole-house heating and cooling
#
# Copyright (c) 2020 Dragan Stancevic <dragan@stancevic.com>
#
# This file is part of heat-ai.
#
# heat-ai is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# heat-ai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with heat-ai. If not, see <https://www.gnu.org/licenses/>.
#
# This python is intended for:
#  Arduino Pro Mini ATmega328P 3.3v,8Mhz
#  with sht3c based temperature/humidity sensor
#  connected to a linux host through USB->serial

from os import fork
from serial import Serial as serialp
import locale
import time
from influxdb import InfluxDBClient as db
import configparser
import errno
locale.setlocale(locale.LC_ALL, '')

# name of our application and config file
name = "sensor01"
cfg_name = "{}.cfg".format(name)

# these are the settings that we require in order to operate,
# config file must have these or we don't start
cfg_must_have = [
	["influxdb", "ip"],
	["influxdb", "port"],
	["influxdb", "user"],
	["influxdb", "password"],
	["influxdb", "dbname"],
	["influxdb", "table"],
	["serial", "port_path"],
	["serial", "port_speed"],
	["sensor", "location"],
	["sensor", "fork"],
	["sensor", "log"],
	["sensor", "log_data"]
]

# load the config file
cfg = configparser.ConfigParser()


def cfg_read():
	"""
	read our configuration file
	:return: None or exit
	"""
	if cfg.read(cfg_name) != [cfg_name]:
		print("error: no valid config in {}".format(cfg_name))
		exit(errno.ENOENT)


def cfg_validate():
	"""
	make sure that none of the settings in config are zero
	:return: None or exit
	"""
	for label in cfg:
		for val in cfg[label]:
			if cfg[label][val] == "":
				print("{}->{} can't be empty".format(label, val))
				exit(errno.EINVAL)


def cfg_check_missing_vals():
	"""
	make sure that we have all the config values we need
	:return: None or exit
	"""
	for mh in cfg_must_have:
		drop_out = True
		for label in cfg:
			for val in cfg[label]:
				if label == mh[0] and val == mh[1]:
					drop_out = False
					continue
		if drop_out is True:
			print("error: missing config {}->{}".format(mh[0], mh[1]))
			exit(errno.EINVAL)


def debug(msg:str):
	"""
	write debug messages on the console or the log
	:param str msg: debug message
	:return: None
	"""
	if cfg['sensor']['fork'] == "yes":
		f = open(cfg['sensor']['log'], "a")
		print("{}: {}".format(time.ctime(), msg), file=f)
		f.close()
	else:
		print("{}: {}".format(time.ctime(), msg))


def data(msg:str):
	"""
	write data messages on the console or the log
	:param str msg: measurements data from sensor
	:return: None
	"""
	if cfg['sensor']['fork'] == "yes":
		if cfg['sensor']['log_data'] == "no":
			return
		f = open(cfg['sensor']['log'], "a")
		print("{}: {}".format(time.ctime(), msg), file=f)
		f.close()
	else:
		print("{}: {}".format(time.ctime(), msg))


class Sensor:
	def __init__(self, serial_port:str, serial_speed:int):
		self.id = None
		self.ver = None
		self.scale = None
		self.serial_port = serial_port
		self.serial_speed = serial_speed
		self.line = None
		self.temperature = None
		self.humidity = None
		self.dispatch = {
			'I:': self.got_info,
			'D:': self.got_data,
			'E:': self.got_error,
		}
		debug("Opening serial port: {}:{}".format(serial_port, serial_speed))
		try:
			self.fd = serialp(serial_port, serial_speed)
		except IOError as e:
			debug("serial port error: {}".format(e))
			exit(e.errno)

	def got_info(self, i:str):
		"""
		This function parses the INFO message from the sensor,
		when we get this, we know the sensor firmware booted
		:param str i: INFO string from the sensor
		:return: send to database; True or False
		"""
		try:
			[_boot, scale, id, version] = i.split(':')
		except Exception as ex:
			debug("got_info({}): {}".format(i, ex))
			time.sleep(1)
			return False
		self.scale = scale
		self.id = id
		self.ver = version
		debug("Temp Sensor Boot: type:{} id:{} version:{}".format(scale, id, version))
		return False

	def got_data(self, d:str):
		"""
		This function parses the DATA message from the sensor;
		these are temperature and humidity measurements
		:param str d: DATA string from the sensor
		:return: send to database; True or False
		"""
		try:
			[id, temperature, humidity] = d.split(':')
		except Exception as ex:
			debug("got_data({}): {}".format(d, ex))
			time.sleep(1)
			return False
		if self.id != id:
			debug("id changed on us from {} to {}".format(self.id, id))
		try:
			self.temperature = float(temperature)
			self.humidity = float(humidity)
		except Exception as ex:
			debug("got_data({}):float_error: {}".format(d, ex))
			time.sleep(1)
			return False
		data("Id {}, Temperature {}f, Humidity {}%".format(id, temperature, humidity))
		return True

	def got_error(self, e:str):
		"""
		This function parses the ERROR message from the sensor;
		these are errors related to communication with the sensor hardware
		:param str e: ERROR string from the sensor
		:return: send to database; True or False
		"""
		debug("ERROR: Id {} - {}".format(self.id, e))
		return False

	def read(self):
		"""
		This function reads a line from the serial port, data
		may come in bursts from the serial but this won't return
		until a new line is received at the end of the data
		:return: None
		"""
		self.line = None
		try:
			data_rd = self.fd.readline()
		except Exception as ex:
			debug("get_data(): {}".format(ex))
			time.sleep(1)
			return
		line = data_rd.decode('ascii')
		self.line = line.strip()

	def process(self):
		"""
		Sensor sends us different types of messages. This function
		dispatches the message, based on message type, to the
		appropriate parsing function
		:return: send to database; True or False
		"""
		if self.line is None:
			return False
		# message type
		m_type = self.line[:2]
		# the actual message
		message = self.line[2:]
		# call the processing function, if there is data to be sent upstream
		# this returns True, otherwise False. NOTE: only got_data can return
		# True, the rest of the functions always return False
		try:
			ret = self.dispatch[m_type](message)
		except Exception as ex:
			debug("received invalid data from sensor: {} : \"{}\"".format(ex, self.line))
			ret = False
			time.sleep(1)
		return ret


cfg_read()
cfg_validate()
cfg_check_missing_vals()

if cfg['sensor']['fork'] == "yes":
	# fork into the background
	if fork() != 0:
		# parent exit
		exit(0)

dbfd = db(
	cfg['influxdb']['ip'], cfg['influxdb']['port'],	cfg['influxdb']['user'],
	cfg['influxdb']['password'], cfg['influxdb']['dbname']
)

# open our temperature sensor
s = Sensor(cfg['serial']['port_path'], int(cfg['serial']['port_speed']))

while True:
	# read sensor
	s.read()
	datetime = time.ctime()
	# process data we got
	if s.process() is True:
		json = [{
			"measurement": cfg['influxdb']['table'],
			"tags": {"location": cfg['sensor']['location'],},
			"time": datetime,
			"fields": {
				"temperature": s.temperature,
				"humidity": s.humidity
				}
		}]

		try:
			# write the data into our database
			dbfd.write_points(json)
		except Exception as ex:
			debug("influxdb error: {}".format(ex))
			time.sleep(1)
			continue
