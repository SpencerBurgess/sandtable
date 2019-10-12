import serial
import socket
import socketserver
import time
import logging
import queue
from tcpserver import *
from threading import Thread, Event 
import json
from Sand import *


#
#   Driver for any kind of board running Marlin software
#
pos = [-1.0,-1.0]
ready = True
        
class MyHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.writer = self.server.writer

    def handle(self):
        data = self.request.recv(1024).strip().split()
        logging.debug( "Data: %s" % data )
        if len(data):
            command = data[0]
            if command != 'status':
                logging.info( "Command: %s" % command )
            if command == '*':
                self.writer.send( " ".join(data[1:]) + '\r') 
            elif command == 'run':
                self.run(data[1],data[2])
            elif command == 'halt':
                self.halt()
            elif command == 'restart':
                self.restart()
        self.request.send(json.dumps({'pos':pos,'state':ready}))

    def run(self,fileName,wait):
        fileName = fileName
        logging.info( "Executing: run %s" % fileName )
        self.writer.flush()

        # FIX: Convert to metric
        with open(fileName, 'r') as file:
            for num,line in enumerate(file):
                self.writer.send(line.upper())
                
        if wait == 'True':
             logging.info( "Waiting for drawing to finish" )
             time.sleep(1.0)
             self.writer.wait()
             logging.debug( "Queue depth is fine, waiting for state %s" % ready )
             while not ready:
                 time.sleep(0.5)
             logging.debug( "Status has changed" )
        logging.info( "Run has completed" )

    def halt(self):
        logging.info( "Executing: halt" )
        self.writer.flush()
        self.writer.send( "M18\r" )
        self.writer.send( "M114\r" )

    def restart(self):
        self.server.stop()


class ReadThread(Thread):
    okCount = 0 

    def __init__(self,ser):
        self.ser = ser
        super(ReadThread, self).__init__()

    def run(self):
        self.running = True
        while self.running:
            line = self.ser.readline().strip()
            if len(line):
                print("<",line)
                if line.startswith('ok'):
                    self.okCount += 1

    def decrement(self):
        self.okCount -= 1

    def stop(self):
        self.running = False


class Writer:
    def __init__(self,ser):
        self.queue = queue.Queue()
        self.writeThread = WriteThread(ser,self.queue)
        self.writeThread.start()
        self.stopFlag = Event()

    def send(self,data):
        self.queue.put(data)

    def flush(self):
        logging.debug( "Flushing queue %d" % self.queue.qsize())
        while not self.queue.empty():
            self.queue.get()
            self.queue.task_done()
        logging.debug( "Done flushing queue" )

    def wait(self):
        self.queue.join()

    def stop(self):
        # FIX: Maybe flush before dying
        self.stopFlag.set()
        self.writeThread.stop()
        # FIX: Maybe send something similar to turn off motors
        # But do it before killing the write thread?
        self.send('abort')

class WriteThread(Thread):
    def __init__(self,ser,queue):
        self.ser = ser
        self.queue = queue
        super(WriteThread, self).__init__()

    def run(self):
        logging.info( "Writer thread active" )
        self.running = True
        while self.running:
            # FIX: Add conditional for controller readiness
            data = self.queue.get()
            self.ser.write(data)
            self.queue.task_done()
        logging.info( "Writer thread exiting" )

    def stop(self):
        self.running = False
 

def runMachine():
    logging.info( 'Starting the sandtable Marlin daemon' )

    # Open the serial port to connect to Marlin
    try:
        ser = serial.Serial(MACHINE_PORT, baudrate=MACHINE_BAUD, rtscts=True, timeout=0.5)
    except Exception as e:
        logging.error( e )
        exit(0)

    # Start the read thread
    reader = ReadThread(ser)
    reader.start()

    # Create the writer
    writer = Writer(ser)

    # Check settings update file
    fullInitialization = True
    with open(MACH_FILE,'r') as f:
        newVersion = f.read()

    try:
        with open(VER_FILE,'r') as f:
            oldVersion = f.read()
        if oldVersion == newVersion:
            fullInitialization = False
    except Exception as e:
        logging.error(e)

    if fullInitialization:
        with open(VER_FILE,'w') as f:
            f.write(newVersion)

    # Initialize the board
    initialize = [
    ]

    # Try not sending the initialization string
    if fullInitialization:
        initialize += machInitialize

    # Add the homing commands
    initialize += ["G28.2X0Y0"]

    for i in initialize:
        writer.send( i + "\r" ) 

    # Start the socket server and listen for requests
    logging.info( "Trying to listen on %s:%d" % (MACH_HOST,MACH_PORT))
    
    retries = 10
    server = None
    while retries > 0:
        try:
            server = StoppableTCPServer((MACH_HOST,MACH_PORT), MyHandler)
            logging.info( "SocketServer connected" )
            break
        except socket.error as e:
            logging.error( "%d retries left: %s" %(retries,e) )
            retries -= 1
            time.sleep(10.0)
    
    if server:
        server.writer = writer
        server.serve()
    logging.info( "Out of server loop!" )
    
    logging.info( "Stopping writer" )
    writer.stop()
    logging.info( "Stopping reader" )
    reader.stop()
    logging.info( "Should be all done. Shut down." )

