#! /usr/bin/env python
# -*- coding: utf-8 -*-
######################################################################################
# API Documentation
# http://www.thinkingcleaner.com/downloads/TC_API.pdf
######################################################################################

import os
import sys
import select
import httplib
import urllib2
import indigo
import math
import decimal
import datetime
import socket
import simplejson as json
from SocketServer import ThreadingMixIn
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import threading


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""

class httpHandler(BaseHTTPRequestHandler):
    def __init__(self, plugin,*args):
        self.plugin = plugin
        self.plugin.debugLog(u"New httpHandler thread: "+threading.currentThread().getName()+", total threads: "+str(threading.activeCount()))
        BaseHTTPRequestHandler.__init__(self,*args)
             
    def triggerEvent(self,eventType,deviceAddress):
        self.plugin.debugLog(u"triggerEvent called")      

    def do_GET(self):
        self.plugin.debugLog(u"Received HTTP GET")        
        self.send_response(200)
        self.end_headers()
        try: 
            ipaddress = str(self.headers.getheader('Local-Ip'))
            self.plugin.sensorUpdateFromWebhook(ipaddress)
        except:
            pass    
 
    def do_POST(self):
        self.plugin.debugLog(u"Received HTTP POST")        
        self.send_response(200)
        self.end_headers()
        try: 
            ipaddress = str(self.headers.getheader('Local-Ip'))
            self.plugin.sensorUpdateFromWebhook(ipaddress)
        except:
            pass    
  


class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Timeout
        self.reqTimeout = 8

        # Pooling
        self.pollingInterval = 2

        # Flag buttonRequest is processing
        self.reqRunning = False

        # create empty device list
        self.deviceList = {}
        self.discoveredList = []

        # Web Hook
        webhookEnabled = False
        webhookPort = 0

        self.sock = None
        self.socketBufferSize = 256
        # install authenticating opener
        self.passman = urllib2.HTTPPasswordMgrWithDefaultRealm()
        authhandler = urllib2.HTTPBasicAuthHandler(self.passman)
        opener = urllib2.build_opener(authhandler)
        urllib2.install_opener(opener)

    def __del__(self):
        indigo.PluginBase.__del__(self)

    ###################################################################
    # Plugin
    ###################################################################

    def deviceStartComm(self, device):
        self.debugLog(device.name + ": Starting device")

        if device.id not in self.deviceList:
            self.deviceList[device.id] = {'ref':device, 'address': device.pluginProps["address"], 'lastTimeSensor':datetime.datetime.now(), 'lastTimeUpdate':datetime.datetime.now()}
            if device.pluginProps.has_key("useAuthentication") and device.pluginProps["useAuthentication"]:
                self.passman.add_password(None, u"http://" + device.pluginProps["address"], device.pluginProps["username"], device.pluginProps["password"])
            self.sensorUpdateFromRequest(device)

    def deviceStopComm(self,device):
        if device.id not in self.deviceList:
            return
        self.debugLog(device.name + ": Stoping device")
        del self.deviceList[device.id]

    def startup(self):
        self.loadPluginPrefs()
        self.debugLog(u"startup called")

        self.reqRunning = False
        socket.setdefaulttimeout(self.reqTimeout)
        
        self.startWebhook()
        

        #self.debugLog("Pooling Interval: " + str(self.pollingInterval))
        #self.debugLog("Request Timeout: " + str(self.reqTimeout))

    def shutdown(self):
        self.debugLog(u"shutdown called")


    def deviceCreated(self, device):
        self.debugLog(u"Created device of type \"%s\"" % device.deviceTypeId)

    def loadPluginPrefs(self):
        # set debug option
        if 'debugEnabled' in self.pluginPrefs:
            self.debug = self.pluginPrefs.get('debugEnabled',False)
        else:
            self.debug = False

        if 'webhookEnabled' in self.pluginPrefs:
            self.webhookEnabled = self.pluginPrefs.get('webhookEnabled',False)
        else:
            self.webhookEnabled = False
            
        if self.webhookEnabled:
            self.webhookPort   = int (self.pluginPrefs.get('webhookPort',8686))
        else:
            self.webhookPort = 0

        self.pollingInterval = 0
        self.reqTimeout = 0

        #if self.pluginPrefs.has_key("pollingInterval"):
        #   self.pollingInterval = int(self.pluginPrefs["pollingInterval"])
        #if self.pollingInterval <= 0:
        #   self.pollingInterval = 30

        #if self.pluginPrefs.has_key("reqTimeout"):
        #   self.reqTimeout = int(self.pluginPrefs['reqTimeout'])
        #if self.reqTimeout <= 0:
        #   self.reqTimeout = 8

        self.reqTimeout = 8
    

    ###################################################################
    # UI Validations
    ###################################################################

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.debugLog(u"validating device Prefs called")
        ipAdr = valuesDict['address'].strip()
        valuesDict['address'] = ipAdr
        errorsDict = indigo.Dict()
        if ipAdr.count('.') != 3:           
            errorsDict['address'] = u"This needs to be a valid IP address."
            return (False, valuesDict, errorsDict)
        if self.validateAddress (ipAdr) == False:           
            errorsDict['address'] = u"This needs to be a valid IP address."
            return (False, valuesDict, errorsDict)
        if (valuesDict['useAuthentication']):
            if not(valuesDict[u'username']>""):                
                errorsDict['username'] = u"Must be filled."
                return (False, valuesDict, errorsDict)
            if not(valuesDict['password']>""):               
                errorsDict['password'] = u"Must be filled."
                return (False, valuesDict, errorsDict)
        return (True, valuesDict)

    def validatePrefsConfigUi(self, valuesDict):
        self.debugLog(u"validating Prefs called")
        if valuesDict[u'webhookEnabled']:
            port = int(valuesDict[u'webhookEnabled'])	
            if (port <= 0 or port>65535):
                errorMsgDict = indigo.Dict()
                errorMsgDict[u'port'] = u"Port number needs to be a valid TCP port (1-65535)."
                return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def closedPrefsConfigUi ( self, valuesDict, UserCancelled):
        #   If the user saves the preferences, reload the preferences
        if UserCancelled is False:
            indigo.server.log ("Preferences were updated, reloading Preferences...")
            if self.pluginPrefs.get('webhookEnabled',False):
                if not (self.webhookPort == int(self.pluginPrefs['webhookPort'])):
                    indigo.server.log("New listen port configured, reload plugin for change to take effect",isError=True)
            self.loadPluginPrefs()
            
    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        if userCancelled is False:
            indigo.server.log ("Device preferences were updated.")
            
    def deviceDiscoverUI(self, valuesDict, typeId, devId):
        validAddress=False
        actualAddress=valuesDict[u'address'].strip()  
        valuesDict[u'address'] = actualAddress
        self.deviceDiscover()
        if not len(self.discoveredList) > 0:
            return valuesDict
            
        if actualAddress:
            for device in self.discoveredList:
                if device['local_ip'] == actualAddress:
                    validAddress=True
        if validAddress:
            return valuesDict
            
        for device in self.discoveredList:
            if not validAddress: 
                valuesDict[u'address'] = device['local_ip']
                validAddress=True
        return valuesDict
      

    def validateAddress (self,value):
        try:
            socket.inet_aton(value)
        except socket.error:
            return False
        return True

    ###################################################################
    # Device discovery
    ###################################################################

    def deviceDiscover (self):        
        self.debugLog("Discovering devices in this LAN")
        try: 
            payloadJson = urllib2.urlopen("https://thinkingsync.com/api/v1/discover/devices").read()
            if payloadJson is None:
                self.debugLog("Discovering devices: nothing received.")
                return False
            else:
                # json == [{"local_ip":"172.30.74.81","uuid":"1976bb832ad681c7","name":"Roomba","device_type":"tc500"}]
                self.discoveredList = json.loads(payloadJson)
                self.debugLog("Discovered " + str(len(self.discoveredList)) + " devices")
                
        except Exception, e:    
            self.debugLog("Discovering devices: Error: " + str(e))  
            return False

        return True


    ###################################################################
    # Web Hook
    ###################################################################
   
    def startWebhook(self):
        if self.webhookEnabled:
            self.myThread = threading.Thread(target=self.listenHTTP, args=())
            self.myThread.daemon = True
            self.myThread.start() 
     
    def listenHTTP(self):
        self.debugLog(u"Starting HTTP listener thread")
        indigo.server.log(u"Listening on TCP port " + str(self.webhookPort))
        self.server = ThreadedHTTPServer(('', self.webhookPort), lambda *args: httpHandler(self, *args))
        self.server.serve_forever()
      
    def sensorUpdateFromWebhook (self,address):
        for deviceId in self.deviceList:
            if self.deviceList[deviceId]['address'] == address:
                self.sensorUpdate(indigo.devices[deviceId], False)
                
    ###################################################################
    # Concurrent Thread
    ###################################################################

    def runConcurrentThread(self):

        self.debugLog(u"Starting polling thread")

        try:
            while True:

                if self.reqRunning == False:
                    todayNow = datetime.datetime.now()
                    for deviceId in self.deviceList:
                        if deviceId in indigo.devices:
                            pollingInterval = 0
                            state           = indigo.devices[deviceId].states["RoombaState"]
                            lastTimeSensor  = self.deviceList[deviceId]['lastTimeSensor']
                            if state == "clean":
                                pollingInterval = 30
                            elif state == "stop":
                                pollingInterval = 30
                            else:
                                pollingInterval = 120
                            nextTimeSensor = lastTimeSensor + datetime.timedelta(seconds=pollingInterval)

                            if nextTimeSensor <= todayNow:
                                #self.debugLog("Thread. Roomba State = " + str(state))
                                #self.debugLog("Thread. Pooling interval = " + str(pollingInterval))
                                #self.deviceList[deviceId]['lastTimeSensor'] = todayNow
                                if self.reqRunning == False:
                                    self.sensorUpdateFromThread(indigo.devices[deviceId])

                self.sleep(0.5)

        except self.StopThread:
            # cleanup
            pass
        self.debugLog(u"Exited polling thread")


    def stopConcurrentThread(self):
        self.stopThread = True
        self.debugLog(u"stopConcurrentThread called")

    ###################################################################
    # HTTP Request against Thinking Cleaner device.
    ###################################################################

    def sendRequest(self, device, urlAction):
        self.reqRunning = True
        requestTrial = 0
        requestMax   = 3
        requestOK    = False

        theUrl = u"http://" + device.pluginProps["address"] + urlAction
        self.debugLog("sending " + theUrl)
        while (requestTrial < requestMax) and (requestOK == False):
            try:
                f = urllib2.urlopen(theUrl)
                requestOK = True
            except Exception, e:
                requestTrial += 1
                lastError = str(e)

        if (requestOK == False):
            self.errorLog(device.name + ": Error: " + lastError)
            self.errorLog(device.name + " did not received the request !")
            self.reqRunning = False
            return False

        self.sleep(1)
        self.sensorUpdateFromRequest(device)
        self.reqRunning = False

        return True

    ########################################################################
    # Retrieve ThinkingCleaner sensor status, and pass them to Indigo device
    ########################################################################

    def sensorUpdateFromRequest (self,device):
        memoReqRunning = self.reqRunning
        self.reqRunning = True
        retValue = self.sensorUpdate(device,True)
        self.reqRunning = memoReqRunning
        return retValue

    def sensorUpdateFromThread (self,device):
        retValue = self.sensorUpdate(device,False)
        return retValue

    def sensorUpdate(self,device,fromRequest):
        needAwake = False
        requestTrial = 0
        requestMax   = 6
        requestOK = False
        lastError = ""

        sTemperature = 0
        sCharge = 0
        sCapacity = 0
        sWeelDrop = False
        sVoltage = 0
        sButton = 0
        sChargingState = "none"
        sCurrent = 0
        sState = 'none'
        sDirt  = 'No'
        sCliff  = 'No'
        sVirtualWall = 'No'
        sObstacle = 'No'
        sVol   = 0
        sVol2  = 0
        sTemp  = 0

        sCleaningTime = 0
        sCleaningTimeTotal = 0
        sCleaningDistance = 0
        sDirtDetected = 0

        sLowPower = 'No'
        sSearchingDock = 'No'
        sDistance = 0
        sBatteryLevel = 0
        sBatteryCond = 0
        sHomebaseDetected = 'No'
        sNearHombebase = 'No'
        sCheckBin = 'No'
        sCleanerState = ""
        payloadJson = ""
        theUrl  = ""


        todayNow = datetime.datetime.now()
        self.deviceList[device.id]['lastTimeSensor'] = todayNow

        self.debugLog(device.name + ": Requesting status.")

        theUrl = u"http://" + device.pluginProps["address"] + "/full_status.json"

        while (requestTrial < requestMax) and (requestOK == False):
            try:
                if fromRequest == False and self.reqRunning == True:
                    return True
                if requestTrial > 0:
                    self.debugLog(device.name + ": Requesting status ... trial #" + str(requestTrial))
                f = urllib2.urlopen(theUrl)
                requestOK = True
                if requestTrial > 0 or device.states["RoombaState"] == "lost":
                    indigo.server.log(device.name + ": was lost, now FOUND !" )

            except Exception, e:
                if fromRequest == False and self.reqRunning == True:
                    return True
                requestTrial += 1
                lastError = str(e)
                if self.stopThread == True:
                    return False
        if self.stopThread == True:
            return False
        if fromRequest == False and self.reqRunning == True:
            return True
        if (requestOK == False):
            self.updateDeviceState(device, "RoombaState", "lost")

            self.debugLog(device.name + ": Error: " + lastError)
            self.errorLog(device.name + " is LOST !")
            return False

        try:
            payloadJson = f.read()
            if payloadJson is None:
                self.errorLog(device.name + ": nothing received.")
                return False
            else:
                payloadDict = dict(json.loads(payloadJson))
                self.debugLog(device.name + ": Status received.")
        except Exception, e:
            self.debugLog("Bad JSON file. ")
            self.debugLog(theUrl)
            self.debugLog(payloadJson)
            return False


        sCleanerState  = payloadDict ['power_status']['cleaner_state']

        if sCleanerState == "st_base":
            sState = 'dock'
            sChargingState = 'notcharging'
            pass
        if sCleanerState == "st_base_recon":
            sState = 'dock'
            sChargingState = 'recovery'
            pass
        if sCleanerState == "st_base_full":
            sState = 'dock'
            sChargingState = 'charging' 
            pass
        if sCleanerState == "st_base_trickle":
            sState = 'dock'
            sChargingState = 'trickle'  
            pass
        if sCleanerState == "st_base_wait":
            sState = 'dock'
            sChargingState = 'waiting'
            pass
        if sCleanerState == "st_plug":
            sState = 'plugged'
            sChargingState = 'notcharging'
            pass
        if sCleanerState == "st_plug_recon":
            sState = 'plugged'
            sChargingState = 'recovery'     
            pass
        if sCleanerState == "st_plug_full":
            sState = 'plugged'
            sChargingState = 'charging' 
            pass
        if sCleanerState == "st_plug_trickle":
            sState = 'plugged'
            sChargingState = 'trickle'          
            pass
        if sCleanerState == "st_plug_wait":
            sState = 'plugged'
            sChargingState = 'waiting'
            pass
        if sCleanerState == "st_stopped":
            sState = 'stop'
            sChargingState = 'notcharging'
            pass
        if sCleanerState == "st_clean":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        if sCleanerState == "st_cleanstop":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        if sCleanerState == "st_clean_spot":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass                    
        if sCleanerState == "st_clean_max":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        if sCleanerState == "st_delayed":
            pass
        if sCleanerState == "st_dock":
            sState = 'clean'
            sChargingState = 'notcharging'  
            sSearchingDock = 'Yes'      
            pass
        if sCleanerState == "st_pickup":
            sState = 'stop'
            sChargingState = 'notcharging'                  
            pass        
        if sCleanerState == "st_remote":
            pass
        if sCleanerState == "st_wait":

            pass
        if sCleanerState == "st_off":
            sState = 'stop'
            sChargingState = 'notcharging'          
            pass
        if sCleanerState == "st_error":
            sState = 'problem'
            sChargingState = 'notcharging'          
            pass            
        if sCleanerState == "st_locate":
            pass
        if sCleanerState == "st_unknown":
            sState = 'problem'
            sChargingState = 'notcharging'          
            pass            

        if int(payloadDict ['tc_status']['cleaning']) > 0:
            sState = 'clean'

        sCleaningTime      = int(payloadDict ['tc_status']['cleaning_time'])
        sCleaningTimeTotal = int(payloadDict ['tc_status']['cleaning_time_total'])
        sCleaningDistance  = int(payloadDict ['tc_status']['cleaning_distance'])
        sDirtDetected      = int(payloadDict ['tc_status']['dirt_detected'])
        sCharge        = int(payloadDict ['power_status']['charge'])
        sCapacity      = int(payloadDict ['power_status']['capacity'])
        sCurrent       = int(payloadDict ['power_status']['current'])
        sBatteryLevel  = int(payloadDict ['power_status']['battery_charge'])
        sBatteryCond   = int(payloadDict ['power_status']['battery_condition'])
        if int(payloadDict ['power_status']['low_power']) > 0:
            sLowPower  = 'Yes'        
        sTemp          = int(payloadDict ['power_status']['temperature'])
        sVol2          = round(decimal.Decimal (str(int(payloadDict ['power_status']['voltage'])/1000.0)),1)

        if int(payloadDict ['sensors']['wall']) > 0:
            sObstacle = 'Yes'
        if int(payloadDict ['sensors']['cliff_left']) > 0:
            sCliff = 'Yes'
        if int(payloadDict ['sensors']['cliff_front_left']) > 0:
            sCliff = 'Yes'
        if int(payloadDict ['sensors']['cliff_right']) > 0:
            sCliff = 'Yes'
        if int(payloadDict ['sensors']['cliff_front_right']) > 0:
            sCliff = 'Yes'
        if int(payloadDict ['sensors']['virtual_wall']) > 0:
            sVirtualWall = 'Yes'

        if int(payloadDict ['sensors']['dirt_detect']) > 0:
            sDirt = 'Yes'

        if int(payloadDict ['sensors']['homebase_detected']) > 0:
            sHomebaseDetected = 'Yes'
        if int(payloadDict ['sensors']['near_homebase']) > 0:
            sNearHombebase = 'Yes'
        if int(payloadDict ['tc_status']['bin_status']) > 0:
            sCheckBin = 'Yes'   

        needAwake = True
        if sTemp > 0:
            needAwake = False
        if sVol2 > 0:
            needAwake = False
        if sBatteryLevel > 0:
            needAwake = False
        #if sChargingState > 0:
        #   needAwake = False
        if sCapacity > 0:
            needAwake = False

        if needAwake == True:
            self.requestAwake (device)
    
        self.updateDeviceState(device, "Dirt", sDirt)
        self.updateDeviceState(device, "Cliff", sCliff)
        self.updateDeviceState(device, "VirtualWall",sVirtualWall)
        self.updateDeviceState(device, "Obstacle",sObstacle)
        self.updateDeviceState(device, "BatteryLevel", sBatteryLevel)
        self.updateDeviceState(device, "BatteryCondition", sBatteryCond)
        self.updateDeviceState(device, "Voltage", sVol2)
        self.updateDeviceState(device, "Temperature", sTemp)
        self.updateDeviceState(device, "WheelDrop", sWeelDrop)
        self.updateDeviceState(device, "LowPower", sLowPower)       

        self.updateDeviceState(device, "HomebaseDetected", sHomebaseDetected)
        self.updateDeviceState(device, "HomebaseNear", sNearHombebase)
        
        self.updateDeviceState(device, "CheckBin", sCheckBin)

        self.updateDeviceState(device, "CleaningTime", sCleaningTime)
        self.updateDeviceState(device, "CleaningTimeTotal", sCleaningTimeTotal)
        self.updateDeviceState(device, "CleaningDistance", sCleaningDistance)
        self.updateDeviceState(device, "DirtDetected", sDirtDetected)
        
        self.updateDeviceState(device, "SearchingDock", sSearchingDock)

        if sChargingState == 'none':
            pass
        else:
            self.updateDeviceState(device, "ChargingState", sChargingState)

        if sState == "none":
            pass
        else:
            if device.states["RoombaState"] != sState:
                indigo.server.log(device.name + ": changed state to " + sState)
                self.updateDeviceState(device, "RoombaState", sState)

        if (sState == "clean") or (sState == "waiting"):
            device.updateStateOnServer("onOffState", True)
        else:
            device.updateStateOnServer("onOffState", False)
    

        for roomba in self.deviceList:
            indigoDevice = self.deviceList[roomba]['ref']
            if device == indigoDevice:
                self.deviceList[roomba]['lastTimeUpdate'] =  datetime.datetime.now()

        return True


    def updateDeviceState(self,device,state,newValue):
        if (newValue != device.states[state]):
            device.updateStateOnServer(key=state, value=newValue)

    def requestAwake(self, device):
        indigo.server.log(device.name + u": Awaking device")
    
    def leaveDock(self, device):
        indigo.server.log(device.name + u": Leaving dock ....")
        self.sendRequest (device,"/command.json?command=leavehomebase")
        self.sleep(5)
        pass        

    ###################################################################
    # Custom Action callbacks
    ###################################################################

    def buttonFindMe(self, pluginAction, device):
        indigo.server.log(device.name + u": Find-me Action called")
        if self.sendRequest (device,"/command.json?command=find_me") == True:
            return True
        else:
            return False

    def buttonLeaveHomeBase(self, pluginAction, device):
        indigo.server.log(device.name + u": Leave Dock Action called")
        if self.sendRequest (device,"/command.json?command=leavehomebase") == True:
            return True
        else:
            return False
         
    def buttonPowerOff(self, pluginAction, device):
        indigo.server.log(device.name + u": Power off Action called")
        if self.sendRequest (device,"/command.json?command=poweroff") == True:
            return True
        else:
            return False   
                
    def buttonClean(self, pluginAction, device):
        indigo.server.log(device.name + u": Clean Action called")
        self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'clean':
            indigo.server.log(device.name + u": Device is also cleaning.")
            return True
        if sState == 'dock':
            self.leaveDock(device)
        if self.sendRequest (device,"/command.json?command=clean") == True:     
            return True
        else:
            return False

    def buttonDock(self, pluginAction, device):
        indigo.server.log(device.name + u": Dock Action called")
        self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'dock':
            indigo.server.log(device.name + u": Roomba is also docked.")
            return True

        self.sendRequest (device,"/command.json?command=dock")
        return True

    def buttonStop(self, pluginAction, device):
        indigo.server.log(device.name + u": Stop Action called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'clean':
            #Clean/Stop works in toggle mode
            self.sendRequest (device,"/command.json?command= poweroff")
            return True

        indigo.server.log(device.name + u": Device is also stopped or docked.")
        return True

    def buttonSpot(self, pluginAction, device):
        indigo.server.log(device.name + u": Spot Action called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba has a problem!")
            return
        self.sendRequest (device,"/command.json?command=spot")

    ###################################################################
    # Relay Action callbacks
    # Trying to define a Roomba as a relay. ON-> Clean. OFF->Dock
    ###################################################################

    def actionControlDimmerRelay(self, pluginAction, device):
        ## Relay ON ##
        if pluginAction.deviceAction == indigo.kDeviceAction.TurnOn:
            if self.buttonClean(pluginAction,device):
                indigo.server.log(u"sent \"%s\" %s" % (device.name, "on"))
            else:
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "on"), isError=True)

        ## Relay OFF ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.TurnOff:
            if self.buttonDock(pluginAction,device):
                indigo.server.log(u"sent \"%s\" %s" % (device.name, "off"))
            else:
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "off"), isError=True)

        ## Relay TOGGLE ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.Toggle:
            if device.onState:
                self.buttonDock(pluginAction,device)
            else:
                self.buttonClean(pluginAction,device)

        ## Relay Status Request ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.RequestStatus:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "status request"))
            if not(self.sensorUpdate (device,True)):
                self.errorLog(u"\"%s\" %s" % (device.name, "status request failed"))
