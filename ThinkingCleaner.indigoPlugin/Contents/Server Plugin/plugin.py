
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
from ghpu import GitHubPluginUpdater
import time



def now_milliseconds():
   return str(int(time.time() * 1000))

def addURLTimeStamp(url):
    newurl = url
    if newurl.find('?') > 0:
        newurl = newurl + '&'
    else:
        newurl = newurl + '?'
    newurl = newurl + '_=' + now_milliseconds()
    return newurl

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""

class httpHandler(BaseHTTPRequestHandler):
    def __init__(self, plugin,*args):
        try:
            self.plugin = plugin
            self.plugin.debugLog(u"WebHook: New httpHandler thread: "+threading.currentThread().getName()+", total threads: "+str(threading.activeCount()))
            BaseHTTPRequestHandler.__init__(self,*args)
        except Exception, e:
            self.plugin.errorLog(u"WebHook: Error: " + str(e))
 
    def do_GET(self):             
        self.receivedMessage()
      
    def do_POST(self):         
        self.receivedMessage()
            
    def receivedMessage(self):    
        try:
            self.send_response(200)
            self.end_headers()       
            ipaddress = str(self.headers.getheader('Local-Ip'))   
            uuid      = str(self.headers.getheader('Uuid')) 
            name      = str(self.headers.getheader('Device-Name'))
            if not ipaddress == 'None': 
                self.plugin.debugLog(u"WebHook: Received HTTP request from '" + ipaddress + "'")  
                hookSource = {"ipaddress":ipaddress,
                    "uuid":uuid,
                    "name":name,
                    "device_type":""}
                    
                self.plugin.sensorUpdateFromWebhook(hookSource) 
            else:
                self.plugin.debugLog(u"WebHook: Received HTTP request from an unknow device")      
        except Exception, e:
            self.plugin.errorLog(u"WebHook: Error: " + str(e))

class keepAliveDaemon (threading.Thread):
    def __init__(self, plugin):
        try:
            super(keepAliveDaemon, self).__init__()
            self.plugin = plugin
            self.plugin.debugLog(u"KeepAlive: New thread.")
        except Exception, e:
            self.plugin.errorLog(u"KeepAlive: Error: " + str(e))

    def run(self):
        self.plugin.debugLog(u"KeepAlive: Starting daemon")
        try:   
            while not self.plugin.keepAliveStop:
            	time.sleep (0.100)              
                for deviceId in self.plugin.deviceList:
                    device = indigo.devices[deviceId]
                    devProps = device.pluginProps
                    if devProps["sleepingproblem"]:
                        state = device.states["RoombaState"]
                        theUrl = addURLTimeStamp (u"http://" + device.pluginProps["address"] + '/status.json')
                        try:
                            data = urllib2.urlopen(theUrl).read()
                        except Exception, e: 
                            pass
                        theUrl = addURLTimeStamp (u"http://" + device.pluginProps["address"] + '/nav.json')
                        try:
                            data = urllib2.urlopen(theUrl).read()
                        except Exception, e: 
                            pass     
        except Exception, e:
            self.plugin.errorLog(u"KeepAlive: Error: " + str(e))   


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.updater = GitHubPluginUpdater(self)
        
        # Timeout
        self.reqTimeout = 8
         # Pooling interval
        self.pollingIntervalDock          = 120
        self.pollingIntervalDockSleep     = 10
        self.pollingIntervalClean         = 30
        self.pollingIntervalCleanSleep    = 5
        self.pollingIntervalSearchingDock = 1
        # Pooling
        self.pollingInterval = 2
        # Flag buttonRequest is processing
        self.reqRunning = False
        # create empty device list
        self.deviceList = {}
        self.discoveredList = []
        # WebHook
        self.webhookEnabled = False
        self.webhookDiscovery = False
        self.webhookPort = 0
        self.sock = None
        self.socketBufferSize = 256
        # KeepAlive
        self.keepAliveEnabled = True
        self.keepAliveStop    = False
        # Discovery
        self.discoveryWorking = False
 
        # Retry
        self.maxRetryLastCommand = 15
       

    def __del__(self):
        indigo.PluginBase.__del__(self)

    ###################################################################
    # Plugin
    ###################################################################

    def deviceCleanForDebug(self,device):
        devProps = device.pluginProps
        devProps.update({
        "uuid":"",
        "address":"172.30.74.83",
        "tcname":"",
        "tcdevicetype":"",
        "autodiscovered":False,
        "undockbeforeclean":False,
        "sleepingproblem": False
        })
        device.replacePluginPropsOnServer(devProps)
        
    def deviceStartComm(self, device):
        self.debugLog(device.name + ": Starting device")
        # Check if device has data generated by deviceDiscovery process
        if not device.pluginProps.has_key("uuid"):
            devProps = device.pluginProps
            devProps["uuid"] = ""
            devProps["tcdevicetype"] = ""
            devProps["autodiscovered"] = False
            devProps["tcname"] = ""
            devProps["undockbeforeclean"] = False
            devProps["sleepingproblem"] = False
            device.replacePluginPropsOnServer(devProps)
        if not device.pluginProps.has_key("tcname"):
            devProps = device.pluginProps
            devProps["tcname"] = ""
            devProps["undockbeforeclean"] = False
            devProps["sleepingproblem"] = False
            device.replacePluginPropsOnServer(devProps)
        if not device.pluginProps.has_key("undockbeforeclean"):    
            devProps = device.pluginProps
            devProps["undockbeforeclean"] = False
            devProps["sleepingproblem"] = False
        if not device.states.has_key("rawCleanerState"):
            device.stateListOrDisplayStateIdChanged()
        if not device.pluginProps.has_key("sleepingproblem"):   
            devProps = device.pluginProps     
            devProps["sleepingproblem"] = False

        #self.deviceCleanForDebug(device)
        self.addDeviceToList(device)

    def addDeviceToList(self, device):
        if device:             
            if device.id not in self.deviceList:
                self.deviceList[device.id] = {
                'ref':device, 
                'address': device.pluginProps["address"], 
                'uuid': device.pluginProps["uuid"], 
                'lastTimeSensor':datetime.datetime.now(), 
                'lastTimeUpdate':datetime.datetime.now(),
                'lastCommand':"",
                'lastCommandCount':0,
                'lastCommandAccomplished':True,
                'lastState':"",
                'lastSearchingDock':"No",
                'sleepingproblem': device.pluginProps["sleepingproblem"]
                }
                self.sensorUpdateFromRequest(device)

    def deleteDeviceFromList(self, device):
        if device:
            if device.id in self.deviceList:
                del self.deviceList[device.id]
    
    def deviceStopComm(self,device):
        if device.id not in self.deviceList:
            return
        self.debugLog(device.name + ": Stoping device")
        self.deleteDeviceFromList (device)   

    def startup(self):
        self.loadPluginPrefs()
        self.debugLog(u"startup called")
        self.reqRunning = False
        socket.setdefaulttimeout(self.reqTimeout)        
        self.startWebhook()
        self.startKeepAlive()
        self.updater.checkForUpdate()

    def shutdown(self):
        self.shutdownKeepAlive()
        self.debugLog(u"shutdown called")

    def deviceCreated(self, device):
        indigo.server.log (u"Created new device \"%s\" of type \"%s\"" % (device.name, device.deviceTypeId))

    def deviceDeleted(self, device):
        indigo.server.log (u"Deleted device \"%s\" of type \"%s\"" % (device.name, device.deviceTypeId))
        self.deleteDeviceFromList (device)

    #def deviceUpdated (self, origDev, newDev):
    #    pass

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
            self.webhookDiscovery = self.pluginPrefs.get('webhookDiscovery',False)
        else:
            self.webhookPort = 0
            self.webhookDiscovery = False         
        self.reqTimeout = 8

        if 'keepAliveEnabled' in self.pluginPrefs:
            self.keepAliveEnabled = self.pluginPrefs.get('keepAliveEnabled',False)
        else:
            self.keepAliveEnabled = False
        self.keepAliveEnabled = True
    
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
                if not (self.webhookPort == int(self.pluginPrefs.get('webhookPort',8686))):
                    indigo.server.log("New listen port configured, reload plugin for change to take effect",isError=True)
            self.loadPluginPrefs()
            
    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        if userCancelled is False:
            indigo.server.log ("Device settings were updated.")
            device = indigo.devices[devId]
            self.deleteDeviceFromList (device)
            self.addDeviceToList (device)

            
    def deviceDiscoverUI(self, valuesDict, typeId, devId):
        if self.discoveryWorking:
            return valuesDict 
        validAddress=False
        actualAddress=valuesDict[u'address'].strip()  
        valuesDict[u'address'] = actualAddress
        self.getDeviceDiscoverList()
        if not len(self.discoveredList) > 0:
            return valuesDict
            
        if actualAddress:
            for discovered in self.discoveredList:
                if discovered['local_ip'] == actualAddress:
                    validAddress=True
        if validAddress:
            return valuesDict
            
        for discovered in self.discoveredList:
            if not validAddress: 
                valuesDict[u'address'] = discovered['local_ip']
                validAddress=True
        return valuesDict
        
    def pluginDiscoverUI(self, valuesDict): 
        if self.discoveryWorking:
            return valuesDict 
        self.deviceDiscover()
        return valuesDict   
      
    def validateAddress (self,value):
        try:
            socket.inet_aton(value)
        except socket.error:
            return False
        return True

    ###################################################################
    # Keep Alive
    ###################################################################

    def startKeepAlive(self):
        if self.keepAliveEnabled:
            try:  
                self.kaThread = keepAliveDaemon(self)
                self.kaThread.daemon = True
                self.kaThread.start() 
            except Exception, e:
                self.errorLog(u"KeepAlive: Error: " + str(e))

    def shutdownKeepAlive(self):
        if self.keepAliveEnabled:
            try: 
                self.keepAliveStop = True
                #self.kaThread.join()
            except Exception, e:
                self.errorLog(u"KeepAlive: Error: " + str(e))
        

    ###################################################################
    # Device discovery
    ###################################################################

    def deviceDiscover (self):
        totalDiscovered = 0
        totalCreated = 0
        totalModified = 0
        totalNotModified = 0
        duplicated = 0
        existingList = {}
        
        if self.discoveryWorking:
            indigo.server.log (u"Other discovery process is running now.")
            return
            
        self.discoveryWorking = True
        indigo.server.log ("Discovering devices in this LAN ...")
        self.getDeviceDiscoverList()
        totalDiscovered = len(self.discoveredList)
        if not totalDiscovered > 0:    
            indigo.server.log ("It was not found any device. Sorry.")
            self.discoveryWorking = False
            return
            
        #existingList = indigo.devices(filter="self.thinkingcleaner")    
        for discovered in self.discoveredList:
            found    = False
            modified = False
            device   = None
            for device in indigo.devices.itervalues(filter="self.thinkingcleaner"):              
                if device.pluginProps["uuid"] == discovered['uuid']:
                    found = True
                    devProps = device.pluginProps
                    if not devProps["address"] == discovered['local_ip'] or not devProps["tcname"] == discovered['name']:
                        devProps["address"] = discovered['local_ip']
                        devProps["tcdevicetype"] = discovered['device_type']
                        devProps["tcname"] = discovered['name']
                        devProps["autodiscovered"] = True
                        devProps["undockbeforeclean"] = False
                        devProps["sleepingproblem"] = False
                        device.replacePluginPropsOnServer(devProps)
                        
                        modified = True
                        indigo.server.log ('Updated existing device "' + device.name + '"')
                       
            if not found:
                for device in indigo.devices.itervalues(filter="self.thinkingcleaner"):   
                    if device.pluginProps["address"] == discovered['local_ip']:
                        found = True
            if not found:  
                newProps = {"ipaddress":discovered['local_ip'],
                    "uuid":discovered['uuid'],
                    "name":discovered['name'],
                    "device_type":discovered['device_type']}
                device = self.createdDiscoveredDevice(newProps)
                self.addDeviceToList (device)
                totalCreated += 1
            else:
               if modified:
                   totalModified += 1
               else:
                   totalNotModified += 1
                
        indigo.server.log (str(totalDiscovered) + " ThinkingCleaner devices discovered.")
        if totalCreated > 0:
            indigo.server.log (str(totalCreated) + " new indigo devices created.")
        if totalModified > 0:
            indigo.server.log (str(totalModified) + " existing indigo devices updated.")
        if totalNotModified > 0: 
            indigo.server.log (str(totalNotModified) + " existing indigo devices not updated.")
        self.discoveryWorking = False
    
    def createdDiscoveredDevice(self,props):
        deviceFolderId = self.getDiscoveryFolder()
        fullName = self.getDiscoveryDeviceName (props['name'],props['uuid'])
        device = indigo.device.create(protocol=indigo.kProtocol.Plugin,
                        address=props['ipaddress'],
                        name=fullName , 
                        description='ThinkingCleaner discovered device', 
                        pluginId="com.tenallero.indigoplugin.thinkingcleaner",
                        deviceTypeId="thinkingcleaner",
                        props={
                            "uuid":props['uuid'], 
                            "tcdevicetype":props['device_type'],  
                            "tcname":props['name'], 
                            "autodiscovered": True,
                            "undockbeforeclean": False,
                            "sleepingproblem": False
                            },
                        folder=deviceFolderId)
        self.addDeviceToList (device)
        return device
        
        
    def getDiscoveryFolder (self):
        deviceFolderName = "ThinkingCleaner"
        if (deviceFolderName not in indigo.devices.folders):
            newFolder = indigo.devices.folder.create(deviceFolderName)
            indigo.devices.folder.displayInRemoteUI(newFolder, value=False)
            indigo.server.log ('Created new device folder "ThinkingCleaner"')
        deviceFolderId = indigo.devices.folders.getId(deviceFolderName)
        return deviceFolderId
        
    def getDiscoveryDeviceName(self,name,uuid):
        if not self.deviceNameExists(name):
            return name
        seedName = name + '-' + uuid
        newName = seedName
        duplicated = 0
        while True:                    
            if not self.deviceNameExists(newName):
                break
            duplicated += 1
            newName = seedName + ' (' + str(duplicated) + ')'
        return newName
                 
    def deviceNameExists (self,name):
        nameFound = False
        for device in indigo.devices:
            if device.name == name:
                nameFound = True
                break
        return nameFound
                                           
    def getDeviceDiscoverList (self):        
        try: 
            payloadJson = urllib2.urlopen("https://thinkingsync.com/api/v1/discover/devices").read()
            if payloadJson is None:
                self.debugLog("Discovering devices: nothing received.")
                return False
            else:
                # json == [{"local_ip":"172.30.74.81","uuid":"1976bb832ad681c7","name":"Roomba","device_type":"tc500"}]
                self.discoveredList = json.loads(payloadJson)
        except Exception, e:    
            self.debugLog("Discovering devices: Error: " + str(e))  
            return False
        return True

    ###################################################################
    # WebHook
    ###################################################################
   
    def startWebhook(self):
        if self.webhookEnabled:
            try:  
                self.whThread = threading.Thread(target=self.listenHTTP, args=())
                self.whThread.daemon = True
                self.whThread.start() 
            except Exception, e:
                self.errorLog(u"WebHook: Error: " + str(e))
     
    def listenHTTP(self):
        self.debugLog(u"WebHook: Starting HTTP listener on port " + str(self.webhookPort))
        try:        
            self.server = ThreadedHTTPServer(('', self.webhookPort), lambda *args: httpHandler(self, *args))
            self.server.serve_forever()
        except Exception, e:
            self.errorLog(u"WebHook: Error: " + str(e))
      
    def sensorUpdateFromWebhook (self, hookSource): 
        found = False       
        for deviceId in self.deviceList:
            if self.deviceList[deviceId]['address'] == hookSource["ipaddress"]:
                device = indigo.devices[deviceId]
                self.debugLog(u"WebHook: The request comes from '" + device.name + "' device")
                self.sensorUpdate(indigo.devices[deviceId], False)
                found = True
                break
        if not found:
            for device in indigo.devices.itervalues(filter="self.thinkingcleaner"): 
                devProps = device.pluginProps
                if devProps["uuid"] == hookSource["uuid"]:
                    if devProps["address"] == hookSource["ipaddress"]:
                        self.debugLog(u"WebHook: The request comes from '" + device.name + u"' device. Indigo communication disabled?")
                        found = True
                        break
                    else:
                        oldValue = devProps["address"]
                        devProps["address"] = hookSource["ipaddress"]
                        device.replacePluginPropsOnServer(devProps)
                        for deviceId in self.deviceList:
                            if self.deviceList[deviceId]['uuid'] == hookSource["uuid"]:
                                self.deviceList[deviceId]['address'] = hookSource["ipaddress"]
                                found = True
                                break
                        
                        indigo.server.log ('Updated address for existing device "' + device.name + '". From ' + oldValue + ' to ' + device['address'])
                        self.sensorUpdate(device, False)
                        break
        if not found and self.webhookDiscovery:
            if not self.discoveryWorking:
                self.discoveryWorking = True
                device = self.createdDiscoveredDevice (hookSource)
                indigo.server.log (u'Created new device "' + device.name + u'". Was detected using WebHook')
                self.discoveryWorking = False
            pass
                    
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
                            if not(self.deviceList [deviceId]['lastCommandAccomplished']):
                                self.retryLastCommand(indigo.devices[deviceId])
                            else:
                                pollingInterval = 0
                                state           = indigo.devices[deviceId].states["RoombaState"]
                                lastTimeSensor  = self.deviceList[deviceId]['lastTimeSensor']
                                if state == "clean":
                                    if indigo.devices[deviceId].states["SearchingDock"] == 'Yes':
                                        pollingInterval = self.pollingIntervalSearchingDock
                                    else:
                                        if self.checkSleepingDevice(indigo.devices[deviceId]):
                                            pollingInterval = self.pollingIntervalCleanSleep
                                        else:
                                            pollingInterval = self.pollingIntervalClean
                                elif state == "stop":
                                    pollingInterval = self.pollingIntervalClean
                                else:
                                    if self.checkSleepingDevice(indigo.devices[deviceId]):
                                        pollingInterval = self.pollingIntervalDockSleep
                                    else:
                                        pollingInterval = self.pollingIntervalDock

                                nextTimeSensor = lastTimeSensor + datetime.timedelta(seconds=pollingInterval)
                                if nextTimeSensor <= todayNow:  
                                    if self.reqRunning == False:
                                        self.sensorUpdateFromThread(indigo.devices[deviceId])

                self.sleep(0.150)

        except self.StopThread:
            # cleanup
            pass
        self.debugLog(u"Exited polling thread")


    def stopConcurrentThread(self):
        self.debugLog(u"stopConcurrentThread called")
        self.stopThread = True
        self.keepAliveStop = True
        #self.shutdown = True
        

    ###################################################################
    # HTTP Request against Thinking Cleaner device.
    ###################################################################

    def cleanLastCommand (self, device):
        self.deviceList [device.id]['lastCommandAccomplished'] = True 
        self.deviceList [device.id]['lastCommand'] = ""
        self.deviceList [device.id]['lastCommandCount'] = 0

    def storeLastCommand (self, device, command):
        if self.checkSleepingDevice(device):
            command = command.strip()
            if not(self.deviceList [device.id]['lastCommand'] == command):
                self.deviceList [device.id]['lastCommand']             = command
                self.deviceList [device.id]['lastCommandCount']        = self.maxRetryLastCommand
                self.deviceList [device.id]['lastCommandAccomplished'] = False
                self.deviceList [device.id]['lastState']               = device.states["RoombaState"]
                self.deviceList [device.id]['lastSearchingDock']       = device.states["SearchingDock"]
        else:
            self.deviceList [device.id]['lastCommand'] = ""       
            self.deviceList [device.id]['lastCommandCount'] = 0            
            self.deviceList [device.id]['lastCommandAccomplished'] = True

    def sendCommand(self, device, command):
        command = command.strip()
        self.storeLastCommand (device, command)
        if self.sendRequest (device,"/command.json?command=" + command) == True:
            return True
        else:
            return False

    def checkSleepingDevice (self,device):
        devProps = device.pluginProps
        if devProps["sleepingproblem"]:
            return True
        else:
            return False

    def checkStateChanged(self, device):
        self.sensorUpdateFromThread (device)
        lastState           = self.deviceList [device.id]['lastState']
        lastSearchingDock   = self.deviceList [device.id]["lastSearchingDock"]
        actualState         = device.states["RoombaState"]
        actualSearchingDock = device.states["SearchingDock"]

        if lastState == actualState:
            if lastSearchingDock == actualSearchingDock:
                return False
            else:
                self.debugLog(device.name + ': SearchingDock changed from "' + lastSearchingDock + '" to "' + actualSearchingDock + '"')
                return True
        else:
            self.debugLog(device.name + ': State changed from "' + lastState + '" to "' + actualState + '"')
            return True

    def checkWishedState(self, device, lastCommand):
    	self.sensorUpdateFromThread (device)
        lastCommandAccomplished  = False
        actualState  = device.states["RoombaState"]
        if lastCommand == 'clean':
            if actualState == 'clean':
                lastCommandAccomplished = True
        elif lastCommand == 'leavehomebase':
            if not(actualState == 'dock'):
                lastCommandAccomplished = True
        elif lastCommand == 'dock':
            if actualState == 'dock':
                lastCommandAccomplished = True
            if actualState == 'waiting':
                lastCommandAccomplished = True
            if device.states["SearchingDock"] == 'Yes':
                lastCommandAccomplished = True
        elif lastCommand == 'poweroff':
            pass
        elif lastCommand == 'spot':
            lastCommandAccomplished = True
        elif lastCommand == 'find_me':
            lastCommandAccomplished = True

        if actualState == 'lost':
            lastCommandAccomplished = True
        if actualState == 'problem':
            lastCommandAccomplished = True
        return lastCommandAccomplished


    def bombDevice (self,device):
        if self.checkStateChanged(device):
            indigo.server.log (device.name + ': Roomba is now awoken!')
            return True
        
        looping       = True
        loopCount     = 0
        lastCommand   = self.deviceList [device.id]['lastCommand']
        stateChanged  = False
        while (looping):
            self.sleep (0.010)
            self.sendRequestOnly (device, "/command.json?command=" + lastCommand + '&' + now_milliseconds() )
            stateChanged = self.checkStateChanged(device)
            loopCount = loopCount + 1
            if loopCount > 30:
                looping = False
            if stateChanged:
                looping = False

        if stateChanged:
            return True
        else:
            return False

    def retryLastCommand(self, device):
        if not(self.checkSleepingDevice(device)):
            self.cleanLastCommand (device)          
            return True
        
        lastCommandCount   = int(self.deviceList [device.id]['lastCommandCount'])
        if not(lastCommandCount > 0):
            self.cleanLastCommand (device) 
            return True

        tryCount           = (self.maxRetryLastCommand - lastCommandCount + 1)
        lastCommand        = self.deviceList [device.id]['lastCommand']
        stateChanged       = self.checkStateChanged(device)

        if not(stateChanged):
            if tryCount > 1:
                indigo.server.log (device.name + ': Resending "' + lastCommand + '" command. Try #' + str(tryCount - 1) )
                if (self.sendRequest (device,"/command.json?command=" + lastCommand)):
                    indigo.server.log (device.name + ': Wait for 8 seconds')
                    for x in range(0, 15):
                        self.sleep (0.500)
                        if self.checkStateChanged(device):
                            stateChanged = True
                            break

        if not(stateChanged):
            if tryCount > 1:
                indigo.server.log ('Trying to awake ' + device.name + ' ...')
                if self.bombDevice(device):
                    stateChanged = True

        if not (stateChanged):
            indigo.server.log (device.name + ': Wait for 8 seconds')
            for x in range(0, 15):
                self.sleep (0.500)
                if self.checkStateChanged(device):
                    stateChanged = True
                    break
        if not(stateChanged):
            if tryCount == 1:
                indigo.server.log (device.name + ': Roomba is asleep!')

        if (stateChanged):
            indigo.server.log (device.name + ': Roomba is awoken!')
            if self.checkWishedState(device,lastCommand):
                self.cleanLastCommand (device) 
                indigo.server.log (device.name + ': "' + lastCommand + '" command accomplished')
                return True
                
        lastCommandCount = lastCommandCount - 1
        self.deviceList [device.id]['lastCommandCount'] = lastCommandCount
        return False

    def sendRequestOnly (self, device, urlAction):
        if device.id not in self.deviceList:
            self.debugLog("Requesting status. Invalid device")
            return False
            
        
        requestTrial = 0
        requestMax   = 3
        requestOK    = False

        theUrl = addURLTimeStamp (u"http://" + device.pluginProps["address"] + urlAction)
        self.debugLog("sending " + theUrl)
        while (requestTrial < requestMax) and (requestOK == False):
            try:
                data = urllib2.urlopen(theUrl).read()
                requestOK = True
            except Exception, e:
                requestTrial += 1
                lastError = str(e)

        if not (requestOK):
            self.errorLog(device.name + ": Error: " + lastError)
            return False
        return True

    def sendRequest (self, device, urlAction):
        self.reqRunning = True
        if self.sendRequestOnly (device,urlAction):
            self.sleep(1)
            self.sensorUpdateFromRequest(device)
            self.reqRunning = False
            return True
        else:
            self.reqRunning = False
            return False

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

        
        if device == None:
            self.debugLog("Requesting status. Invalid device")
            return False
        
        if device.id not in self.deviceList:
            self.debugLog("Requesting status. Invalid device")
            return False
            
            
        todayNow = datetime.datetime.now()
        self.deviceList[device.id]['lastTimeSensor'] = todayNow

        self.debugLog(device.name + ": Requesting status.")

        theUrl = addURLTimeStamp(u"http://" + device.pluginProps["address"] + "/full_status.json")

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
                    device.setErrorStateOnServer(None)

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
            device.setErrorStateOnServer('Lost')
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
        elif sCleanerState == "st_base_recon":
            sState = 'dock'
            sChargingState = 'recovery'
            pass
        elif sCleanerState == "st_base_full":
            sState = 'dock'
            sChargingState = 'charging' 
            pass
        elif sCleanerState == "st_base_trickle":
            sState = 'dock'
            sChargingState = 'trickle'  
            pass
        elif sCleanerState == "st_base_wait":
            sState = 'dock'
            sChargingState = 'waiting'
            pass
        elif sCleanerState == "st_plug":
            sState = 'plugged'
            sChargingState = 'notcharging'
            pass
        elif sCleanerState == "st_plug_recon":
            sState = 'plugged'
            sChargingState = 'recovery'     
            pass
        elif sCleanerState == "st_plug_full":
            sState = 'plugged'
            sChargingState = 'charging' 
            pass
        elif sCleanerState == "st_plug_trickle":
            sState = 'plugged'
            sChargingState = 'trickle'          
            pass
        elif sCleanerState == "st_plug_wait":
            sState = 'plugged'
            sChargingState = 'waiting'
            pass
        elif sCleanerState == "st_stopped":
            sState = 'stop'
            sChargingState = 'notcharging'
            pass
        elif sCleanerState == "st_clean":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        elif sCleanerState == "st_cleanstop":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        elif sCleanerState == "st_clean_spot":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass                    
        elif sCleanerState == "st_clean_max":
            sState = 'clean'
            sChargingState = 'notcharging'          
            pass
        elif sCleanerState == "st_delayed":
            pass
        elif sCleanerState == "st_dock":
            sState = 'clean'
            sChargingState = 'notcharging'  
            sSearchingDock = 'Yes'      
            pass
        elif sCleanerState == "st_pickup":
            sState = 'stop'
            sChargingState = 'notcharging'                  
            pass        
        elif sCleanerState == "st_remote":
            pass
        elif sCleanerState == "st_wait":
            pass
        elif sCleanerState == "st_off":
            sState = 'stop'
            sChargingState = 'notcharging'          
            pass
        elif sCleanerState == "st_error":
            sState = 'problem'
            sChargingState = 'notcharging'          
            pass            
        elif sCleanerState == "st_locate":
            pass
        elif sCleanerState == "st_unknown":
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

        self.updateDeviceState(device, "rawCleanerState",sCleanerState)

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

        uiValue = sState
        if uiValue == 'dock':
            if sBatteryLevel < 100:
                uiValue = uiValue + ' ' + str(sBatteryLevel) + '%'
        elif uiValue == 'clean':
            if sSearchingDock == 'Yes':
                uiValue = 'searching'
                
        if (sState == "clean") or (sState == "waiting"):
            device.updateStateOnServer("onOffState", True, uiValue=uiValue)
        else:
            device.updateStateOnServer("onOffState", False, uiValue=uiValue)
    
        uuid   = payloadDict ['firmware']['uuid']
        tctype = payloadDict ['tc_status']['modelnr'] 
        
        otherSameUuid = False
        if not device.pluginProps["tcdevicetype"]:
            devProps = device.pluginProps
            devProps.update({"tcdevicetype":tctype})
            device.replacePluginPropsOnServer(devProps)
        if not device.pluginProps["uuid"]:
            for other in self.deviceList:
                if self.deviceList[other]['uuid'] == uuid:
                     otherSameUuid = True            
            if not otherSameUuid:
                devProps = device.pluginProps
                devProps.update({"uuid":uuid})
                device.replacePluginPropsOnServer(devProps)
                for roomba in self.deviceList:
                    indigoDevice = self.deviceList[roomba]['ref']
                    if device == indigoDevice:
                        self.deviceList[roomba]['uuid'] = uuid
                
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
        self.sendCommand (device,'leavehomebase')
        self.sleep(5)        
        pass        

    ###################################################################
    # Custom Action callbacks
    ###################################################################


    def buttonRestart(self, device):
        indigo.server.log(device.name + u": Restart Action called")
        if self.sendRequest (device,"/command.json?command=crash") == True:
            indigo.server.log(device.name + u": Restarting ...")
            self.sleep (30)
            self.sensorUpdateFromThread (device)
            return True
        else:
            return False

    def buttonFindMe(self, pluginAction, device):
        indigo.server.log(device.name + u": Find-me Action called")
        if self.sendRequest (device,"/command.json?command=find_me") == True:           
            return True
        else:
            return False

    def buttonLeaveHomeBase(self, pluginAction, device):
        indigo.server.log(device.name + u": Leave Dock Action called")
        #if self.sendRequest (device,"/command.json?command=leavehomebase") == True:
        if self.sendCommand (device,'leavehomebase') == True:
            return True
        else:
            return False
         
    def buttonPowerOff(self, pluginAction, device):
        indigo.server.log(device.name + u": Power off Action called")
        if self.sendCommand (device,"poweroff") == True:
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
            devProps = device.pluginProps
            if devProps["undockbeforeclean"]:
                self.leaveDock(device)
        if self.sendCommand (device,'clean') == True:       
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
        if self.sendCommand (device,'dock') == True:
            return True
        else:           
            return False

    def buttonStop(self, pluginAction, device):
        indigo.server.log(device.name + u": Stop Action called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'clean':
            #Clean/Stop works in toggle mode
            self.sendCommand (device,'poweroff')            
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
        self.sendCommand (device,"spot")

    ###################################################################
    # Relay Action callbacks
    # Trying to define a Roomba as a relay. ON-> Clean. OFF->Dock
    ###################################################################

    def actionControlDimmerRelay(self, pluginAction, device):
        ## Relay ON ##
        if pluginAction.deviceAction == indigo.kDeviceAction.TurnOn:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "on"))
            if not self.buttonClean(pluginAction,device):        
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "on"), isError=True)

        ## Relay OFF ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.TurnOff:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "off"))
            if not self.buttonDock(pluginAction,device):             
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

    ########################################
    # Menu Methods
    ########################################
    def toggleDebugging(self):
        if self.debug:
            indigo.server.log("Turning off debug logging")
            self.pluginPrefs["debugEnabled"] = False                
        else:
            indigo.server.log("Turning on debug logging")
            self.pluginPrefs["debugEnabled"] = True
        self.debug = not self.debug
        return
        
    def menuDeviceDiscovery(self):
        if self.discoveryWorking:
            return
        self.deviceDiscover()
        return
        
    def checkForUpdates(self):
        update = self.updater.checkForUpdate() 
        if (update != None):
            pass
        return    

    def updatePlugin(self):
        self.updater.update()
