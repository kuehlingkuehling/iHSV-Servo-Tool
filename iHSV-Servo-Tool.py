#!/usr/bin/env python3
#
# iHSV Servo Tool
# Copyright (C) 2018 Robert Budde

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtSerialPort import QSerialPortInfo

import pyqtgraph as pg

from iHSV_Properties import iHSV

import os
import numpy as np
import serial
import minimalmodbus

import xml.etree.ElementTree as ET
from xml.dom import minidom


class ModBusDataCurveItem(pg.PlotCurveItem):

    signalIsActive = pyqtSignal(pg.PlotCurveItem, name='IsActive')
    signalAttachToAxis = pyqtSignal(pg.PlotCurveItem, name='AttachToAxis')

    def __init__(self, name='None', registers=[], signed=False, settings=None):
        super().__init__(connect="finite", name=name)

        self.registers = registers
        self.signed = signed
        self.settings = settings
        self.color = QColor(255, 255, 255)
        self.widget = QWidget()
        layout = QGridLayout(self.widget)
        self.colorButton = QPushButton()
        self.colorButton.setFixedWidth(20)
        self.colorButton.setFixedHeight(20)
        self.colorButton.clicked.connect(self.chooseColor)
        self.label = QLabel(self.name())
        self.activeCheckbox = QCheckBox('Active')
        self.activeCheckbox.toggled.connect(self.setActive)
        self.axisCheckbox = QCheckBox('2nd Y')
        self.axisCheckbox.toggled.connect(self.attachToAxis)
        layout.addWidget(self.colorButton, 0, 0, 1, 2)
        layout.setColumnMinimumWidth(0, 30)
        layout.addWidget(self.label, 0, 1, 1, 2)
        layout.setColumnMinimumWidth(1, 150)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self.activeCheckbox, 0, 2)
        layout.addWidget(self.axisCheckbox, 1, 2)
        layout.setColumnMinimumWidth(2, 50)
        layout.setColumnStretch(2, 1)

        self.readSettings()

    def readSettings(self):
        try:
            self.setColor(self.settings.value(self.name() + "/Color", QColor(255,255,255)))
            self.activeCheckbox.setChecked(self.settings.value(self.name() + "/Active", False, type=bool))
            self.axisCheckbox.setChecked(self.settings.value(self.name() + "/2ndAxis", False, type=bool))
        except:
            pass

    def writeSettings(self):
        try:
            self.settings.setValue(self.name() + "/Color", self.color)
            self.settings.setValue(self.name() + "/Active", self.activeCheckbox.isChecked())
            self.settings.setValue(self.name() + "/2ndAxis", self.axisCheckbox.isChecked())
        except:
            pass

    def setColor(self, color):
        if color.isValid():
            self.color = color
            self.colorButton.setStyleSheet("QPushButton { background-color: %s }" % (color.name()))
            pen = pg.mkPen(self.color, width=2)
            self.setPen(pen)

    def chooseColor(self):
        color = QColorDialog.getColor(self.color)
        self.setColor(color)

    def setActive(self):
        self.setData()
        self.signalIsActive.emit(self)

    def isActive(self):
        return self.activeCheckbox.isChecked()

    def attachToAxis(self):
        self.signalAttachToAxis.emit(self)

    @property
    def On2ndAxis(self):
        return self.axisCheckbox.isChecked()

    def appendData(self, rawValues):
        if len(rawValues) == 2:
            value = (rawValues[0] << 16) | rawValues[1]
            if (0x80000000 & value): 
                value = - (0x0100000000 - value)
        elif self.signed:
            value = rawValues[0]
            if (0x8000 & value): 
                value = - (0x010000 - value)
        else:
            value = rawValues[0]

        if (self.yData is None):
            self.setData([value])
            self.setPos(0, 0)
        elif (len(self.yData) <= 1000):
            self.setData(np.append(self.yData, value))
            self.setPos(-len(self.yData)+1, 0)
        else:
            self.yData = np.roll(self.yData,-1)
            self.yData[-1] = value
            # avoid copying data - xData etc. remain the same
            self.path = None # required to trigger path update
            self.update()
            self.sigPlotChanged.emit(self)

    def getRegisters(self):
        return self.registers


class MainWindow(QMainWindow):

    def __init__(self):
        super(MainWindow, self).__init__()

        self.setWindowTitle("iHSV57 Servo Tool")

        self.settings = QSettings("IBB", "iHSV57 Servo Tool")
        self.connected = False

        self.motorversion = 'v6'
        self.ihsv = iHSV(self.motorversion)

        ## Create some widgets to be placed inside
        self.cbSelectMotorVersion = QComboBox()
        self.cbSelectComport = QComboBox()
        self.pbOpenCloseComport = QPushButton('Open Comport')
        self.pbOpenCloseComport.clicked.connect(self.openCloseComport)
        self.pbReadParams = QPushButton('Read Parameters')
        self.cbSelectParameterGroup = QComboBox()
        self.pbReadParams.clicked.connect(self.readParams)
        self.pbStartStopMonitor = QPushButton('Start Monitor')
        self.pbStartStopMonitor.setFixedHeight(80)
        self.pbStartStopMonitor.clicked.connect(self.startStopMonitor)
        self.pbExport = QPushButton('Export Parameters to File')
        self.pbExport.clicked.connect(self.exportToFileDialog)
        self.pbLoadParams = QPushButton('Load Parameters from File')
        self.pbLoadParams.clicked.connect(self.loadParamsFromFileDialog)
        self.cbLoadGainsOnly = QCheckBox("Only load gain relevant parameters effective immediately")
        self.cbLoadGainsOnly.setChecked(True)

        self.ParamTable = QTableWidget(1, 1, self)

        pg.setConfigOptions(antialias=False)
        self.plot = pg.PlotWidget()
        self.plot.setDownsampling(mode='peak')
        self.plot.setClipToView(True)
        self.plot.setXRange(-100, 0)
        self.plot.setYRange(-200, 200)
        self.plot.setLimits(xMin=-1000, xMax=0, minXRange=20, maxXRange=1000)
        self.plot.setLabel('bottom', text='Time', units='s')
        self.plot.getAxis('bottom').setScale(0.01)
        self.plot.showAxis('right')

        self.plot2ndAxis = pg.ViewBox()
        self.plot.scene().addItem(self.plot2ndAxis)
        self.plot.getAxis('right').linkToView(self.plot2ndAxis)
        self.plot2ndAxis.setXLink(self.plot)
        self.plot2ndAxis.setYRange(-10, 10)

        def updateViews():
            self.plot2ndAxis.setGeometry(self.plot.getViewBox().sceneBoundingRect())
            self.plot2ndAxis.linkedViewChanged(self.plot.getViewBox(), self.plot2ndAxis.XAxis)

        updateViews()
        self.plot.getViewBox().sigResized.connect(updateViews)

        self.vbox = QVBoxLayout()

        self.getDataPlots()

        self.groupBox = QGroupBox('Data plots')
        self.vbox.addStretch(1)
        self.groupBox.setLayout(self.vbox)

        ## Define a top-level widget to hold everything
        self.widget = QWidget()

        ## Create a grid layout to manage the widgets size and position
        layout = QGridLayout(self.widget)

        ## Add widgets to the layout in their proper positions
        layout.addWidget(self.plot, 0, 0, 1, 2)  # plot goes on top, spanning 2 columns
        layout.addWidget(self.groupBox, 0, 2)  # legend to the right
        layout.setColumnMinimumWidth(0, 200)
        layout.setColumnStretch(1, 1)
        layout.setColumnMinimumWidth(1, 200)
        layout.setColumnMinimumWidth(2, 250)
        layout.addWidget(self.cbSelectMotorVersion, 1, 0)  # MotorVersion-combobox goes in upper-left
        layout.addWidget(self.cbSelectComport, 2, 0)   # comport-combobox goes in 2nd upper-left
        layout.addWidget(self.pbOpenCloseComport, 3, 0)   # open/close button goes in middle-left
        layout.addWidget(self.cbSelectParameterGroup, 4, 0)  # parameter-group-combobox
        layout.addWidget(self.pbReadParams, 5, 0)
        layout.addWidget(self.pbStartStopMonitor, 6, 0)
        layout.addWidget(self.pbExport, 7, 0)
        layout.addWidget(self.pbLoadParams, 8, 0)
        layout.addWidget(self.cbLoadGainsOnly, 9, 0)  # Add checkbox below the "Load Parameters from File" button
        layout.addWidget(self.ParamTable, 1, 1, 8, 2)  # list widget goes in bottom-left

        self.setCentralWidget(self.widget)

        self.createActions()

        self.cbSelectMotorVersion.addItems(self.ihsv.get_supported_motor_versions())

        self.cbSelectMotorVersion.currentTextChanged.connect(self.onMotorVersionChange)

        ## Call function to create initally the widgets depending on motorversion
        self.onMotorVersionChange()

        comports = QSerialPortInfo.availablePorts()
        for comport in comports:
            port = comport.portName()
            if os.path.exists(os.path.join("/dev", port)):
                port = os.path.join("/dev", port)
            self.cbSelectComport.addItem(port)

        self.readSettings()
        
        self.statusBar().showMessage("Ready", 2000)

    def onMotorVersionChange(self):
        self.motorversion = self.ihsv.supported_motor_versions[str(self.cbSelectMotorVersion.currentText())]
        self.ihsv = iHSV(self.motorversion)

        self.getDataPlots()

        self.cbSelectParameterGroup.clear()
        self.cbSelectParameterGroup.addItems(self.ihsv.get_parameter_group_list())

        self.createParameterTable()

    def getDataPlots(self):
        self.curves = []

        # remove all widgets from vbox layout
        for i in reversed(range(self.vbox.count())):
            try:
                self.vbox.itemAt(i).widget().setParent(None)
            except AttributeError:
                pass

        for liveDataInfo in self.ihsv.get_live_data_list():
            regs = liveDataInfo[0]
            curve = ModBusDataCurveItem(liveDataInfo[2], regs, liveDataInfo[1], settings=self.settings)
            curve.signalAttachToAxis.connect(self.attachCurve)
            curve.attachToAxis()
            self.curves += [curve]
            self.vbox.addWidget(curve.widget)

    def createParameterTable(self):
        header = self.ihsv.get_selected_motor_parameter()
        self.ParamTable.setColumnCount(len(header))
        self.ParamTable.setRowCount(20)
        self.ParamTable.setHorizontalHeaderLabels(header)
        self.ParamTable.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.ParamTable.verticalHeader().setVisible(False)

        for col_nbr, col_name in enumerate(header):
            if col_name == 'Name':
                self.ParamTable.horizontalHeader().setSectionResizeMode(col_nbr, QHeaderView.Stretch)
            else:
                self.ParamTable.horizontalHeader().setSectionResizeMode(col_nbr, QHeaderView.ResizeToContents)

    def attachCurve(self, curve):
        try:
            if curve.On2ndAxis:
                if curve in self.plot.listDataItems():
                    self.plot.removeItem(curve)
                self.plot2ndAxis.addItem(curve)
            else:
                if curve in self.plot.listDataItems():
                    self.plot2ndAxis.removeItem(curve)
                self.plot.addItem(curve)
        except:
            print('Error attaching curve')

    def openCloseComport(self):
        if not self.connected:
            try:
                self.servo = minimalmodbus.Instrument(self.cbSelectComport.currentText(), 1)
                self.servo.serial.baudrate = self.ihsv.get_rs232_settings('baudrate')
                self.servo.serial.bytesize = self.ihsv.get_rs232_settings('bytesize')
                self.servo.serial.parity   = self.ihsv.get_rs232_settings('parity')
                self.servo.serial.stopbits = self.ihsv.get_rs232_settings('stopbits')
                self.servo.serial.timeout  = self.ihsv.get_rs232_settings('timeout')
            except Exception as e:
                print(e)
                self.statusBar().showMessage("Failed to open port", 2000)
                return
            try:
                if not self.servo.serial.isOpen():
                    self.servo.serial.open()
                self.servo.read_register(0x80)
                self.statusBar().showMessage("Port opened successfully", 2000)
                self.pbOpenCloseComport.setText('Close Comport')
                self.connected = True
            except Exception as e:
                print(e)
                self.servo.serial.close()
                self.statusBar().showMessage("Device does not respond", 2000)
                return
        else:
            if (self.pbStartStopMonitor.text() == 'Stop Monitor'):
                self.startStopMonitor()
            try:
                self.servo.serial.close()
                self.statusBar().showMessage("Port closed", 2000)
            except Exception as e:
                print(e)
                pass
            self.pbOpenCloseComport.setText('Open Comport')
            self.connected = False

    def readParams(self):
        if not self.connected:
            return
        try:
            self.ParamTable.cellChanged.disconnect(self.writeParams)
        except Exception as e:
            print(e)
            pass
        self.statusBar().showMessage("Loading System Params...", 2000)
        par_list = self.ihsv.get_parameter_list([self.cbSelectParameterGroup.currentText()])
        self.ParamTable.setRowCount(len(par_list))
        row = 0

        # lists to store address and decimal factor of each row -> necessary for writeParams
        self.ParamTable.addressList = []
        self.ParamTable.decimalList = []
        for configDataInfo in par_list:
            reg = int(configDataInfo['Address'], 16)
            self.ParamTable.addressList.append(reg)
            val = self.servo.read_register(reg)
            print(f"Reading register 0x{reg:02x} returned {val}")
            # convert negative values back from two's complement representation
            if float(configDataInfo['Default']) < 0:
                # convert to signed 16-bit integer
                val = val - (1 << 16) if val >= (1 << 15) else val
            # move decimal point
            if 'decimal_place' in configDataInfo.keys():
                decimal = int(configDataInfo['decimal_place'])
                if decimal != 0:
                    val /= 10**int(configDataInfo['decimal_place'])
                self.ParamTable.decimalList.append(decimal)
            else:
                self.ParamTable.decimalList.append(0)
            print(f"with decimal places it is {val}")
            configDataInfo['Value'] = val
            for col, par in enumerate(self.ihsv.get_selected_motor_parameter()):
                if par == 'Description':
                    # Set the text "[description]" with blue, underlined style and a tooltip
                    description_text = str(configDataInfo.get(par, ""))
                    item = QTableWidgetItem("[description]")
                    item.setToolTip(description_text)  # Tooltip with the actual description
                    item.setFlags(Qt.ItemIsEnabled)  # Make the cell non-editable
                    item.setForeground(QBrush(QColor("blue")))  # Set text color to blue
                    font = item.font()
                    font.setUnderline(True)  # Underline the text
                    item.setFont(font)
                    item.setTextAlignment(Qt.AlignCenter)  # Center the text
                    self.ParamTable.setItem(row, col, item)
                else:
                    item = QTableWidgetItem(str(configDataInfo[par]))
                    if par != 'Value':
                        # item.setBackground(QColor('lightgrey'))
                        item.setFlags(Qt.ItemIsEditable)
                    try:
                        # check if data is a number
                        float(item.text())
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignTop)

                    except ValueError:
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)

                    self.ParamTable.setItem(row, col, item)
            row += 1
        self.ParamTable.resizeRowsToContents()
        self.ParamTable.cellChanged.connect(self.writeParams)
        self.statusBar().showMessage("Loading System Params done!", 2000)

    def writeParams(self, row, column):
        if not self.connected:
            return
        if self.ParamTable.horizontalHeaderItem(column).text() != 'Value':
            return
        try:
            value = self.ParamTable.item(row, column).text()

            # move decimal point
            if self.ParamTable.decimalList[row] != 0:
                value = float(value) * 10 ** self.ParamTable.decimalList[row]
            value = int(value)
        except Exception as e:
            print(e)
            self.statusBar().showMessage("Failed to convert Config Value...", 2000)
            return
        reg = self.ParamTable.addressList[row]
        print(f"Writing {value} to register 0x{reg:02x}")
        reg = reg | 0x8000 # whatever this extra bit does, JMC software uses it when writing to a register
        
        # Convert negative values to two's complement representation
        if value < 0:
            value = (1 << 16) + value  # Convert to unsigned 16-bit integer

        self.servo.write_register(reg, value, functioncode=6)
        # pause to ensure the write is processed
        QThread.msleep(10)
        self.servo.read_register(reg) # reading the register again seems to make sure the value is persistent through power off...
        self.statusBar().showMessage("Writing {0} to 0x{1:02x} done!".format(value, reg), 5000)

    def exportToFileDialog(self):
        if not self.connected:
            self.statusBar().showMessage("Not connected...", 2000)
            return
        options = QFileDialog.Options()
        suggested_filename = "parameters.xml"  # Default suggested filename
        fileName, _ = QFileDialog.getSaveFileName(self, "Save Parameters As", suggested_filename, "XML Files (*.xml);;All Files (*)", options=options)
        if fileName:
            QApplication.processEvents()  # Ensures the dialog closes immediately
            if not fileName.endswith('.xml'):
                fileName += '.xml'
            self.saveParamsAsXML(fileName)

    def saveParamsAsXML(self, fileName):
        root = ET.Element('Parameters')

        # Create a progress dialog
        progress = QProgressDialog("Saving parameters...", "Cancel", 0, self.cbSelectParameterGroup.count(), self)
        progress.setWindowTitle("Saving")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Loop through all parameter groups
        for i in range(self.cbSelectParameterGroup.count()):
            group = self.cbSelectParameterGroup.itemText(i)
            progress.setLabelText(f"Processing {group}...")
            progress.setValue(i)

            if progress.wasCanceled():
                break

            # Select the group and read the parameters
            self.cbSelectParameterGroup.setCurrentIndex(i)
            self.readParams()

            # Add parameters to XML
            groupElement = ET.SubElement(root, group)
            for row in range(self.ParamTable.rowCount()):
                paramElement = ET.SubElement(groupElement, 'Parameter')
                for col in range(self.ParamTable.columnCount()):
                    header = self.ParamTable.horizontalHeaderItem(col).text()
                    cell = self.ParamTable.item(row, col).text()
                    ET.SubElement(paramElement, header).text = cell
                # save decimal place info in file
                ET.SubElement(paramElement, "decimal_place").text = str(self.ParamTable.decimalList[row])

        progress.setValue(self.cbSelectParameterGroup.count())

        # Convert the ElementTree to a string and format it
        rough_string = ET.tostring(root, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        pretty_xml_as_string = reparsed.toprettyxml(indent="  ")

        # Write the formatted XML to the file
        with open(fileName, 'w', encoding='utf-8') as xmlFile:
            xmlFile.write(pretty_xml_as_string)

    def loadParamsFromFileDialog(self):
        if not self.connected:
            self.statusBar().showMessage("Not connected...", 2000)
            return
        options = QFileDialog.Options()
        fileName, _ = QFileDialog.getOpenFileName(self, "Load Parameters from XML", "", "XML Files (*.xml);;All Files (*)", options=options)
        if fileName:
            QApplication.processEvents()  # Ensures the dialog closes immediately
            if not os.path.exists(fileName):
                print(f"File does not exist: {fileName}")
                return
            self.writeParamsFromXML(fileName)

    def writeParamsFromXML(self, fileName):
        tree = ET.parse(fileName)
        root = tree.getroot()

        # Create a progress dialog
        progress = QProgressDialog("Loading parameters...", "Cancel", 0, len(root), self)
        progress.setWindowTitle("Loading")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Iterate through all parameter groups in the XML
        for i, groupElement in enumerate(root):
            progress.setLabelText(f"Processing {groupElement.tag}...")
            progress.setValue(i)

            if progress.wasCanceled():
                break

            for paramElement in groupElement.findall('Parameter'):
                # Skip parameters if "Only load gains parameters effective immediately" is checked
                if self.cbLoadGainsOnly.isChecked():
                    param_code = paramElement.find('Code')
                    if param_code is not None and param_code.text not in self.ihsv.gain_relevant_only_registers:
                        # If the parameter is not gain relevant, skip it
                        continue

                reg = int(paramElement.find('Address').text, 16)
                value = float(paramElement.find('Value').text)

                # Check if 'decimal_place' exists and handle accordingly
                decimal_place_element = paramElement.find('decimal_place')
                if decimal_place_element is not None and decimal_place_element.text.isdigit():
                    decimal_place = int(decimal_place_element.text)
                    if decimal_place != 0:
                        value *= 10 ** decimal_place
                value = int(value)

                # Convert negative values to two's complement representation
                if value < 0:
                    value = (1 << 16) + value  # Convert to unsigned 16-bit integer

                try:
                    param_name = paramElement.find('Name').text if paramElement.find('Name') is not None else f"Param_{reg:02x}"
                    print(f"Writing {value} to register 0x{reg:02x} ({param_name})")
                    reg = reg | 0x8000 # whatever this extra bit does, JMC software uses it when writing to a register
                    self.servo.write_register(reg, value, functioncode=6)
                    # pause to ensure the write is processed
                    QThread.msleep(10)
                    self.servo.read_register(reg) # reading the register again seems to make sure the value is persistent through power off...
                    self.statusBar().showMessage("Writing {0} to 0x{1:02x} done!".format(value, reg), 5000)
                except Exception as e:
                    self.statusBar().showMessage(f"Error writing {value} to 0x{reg:02x}: {str(e)}", 5000)

        progress.setValue(len(root))

    def updateCurves(self):
        try:
            # get dictionary of active curves and their registers
            curves_regs = {curve: curve.getRegisters() for curve in self.curves if curve.isActive()}
            #print(curves_regs)
            if (len(curves_regs) == 0):
                return

            # get list of all registers that need to be read
            regs_list = sorted([reg for regs in curves_regs.values() for reg in regs])
            #print(regs_list)

            # get list of aggregated lists (tolerate gaps of up to 2 regs)
            regs_aggr = np.split(regs_list, np.where(np.diff(regs_list) > 3)[0]+1)
            # intermediate step to fill in gaps if gaps were allowed
            regs_aggr = [range(regs_range[0], regs_range[-1]+1) for regs_range in regs_aggr]
            #print(regs_aggr)

            # use aggregated regs to read all values and create dictionary with reg:value pairs
            if self.connected:
                regs_values = dict([reg_value for regs in regs_aggr for reg_value in zip(regs, self.servo.read_registers(int(regs[0]), len(regs)))])
            else:
                regs_values = dict([reg_value for regs in regs_aggr for reg_value in zip(regs, [int(value*100) for value in np.random.randn(len(regs))])])
            #print(regs_values)

            # iterate active curves and use associated regs to look up values
            for curve,regs in curves_regs.items():
                values = [regs_values[reg] for reg in regs] 
                curve.appendData(values)
        except Exception as e:
            print(f'Error updating data: {e}')

    def startStopMonitor(self):
        if (self.pbStartStopMonitor.text() == 'Start Monitor'):
            self.monitorTimer = QTimer()
            self.monitorTimer.timeout.connect(self.updateCurves)
            self.monitorTimer.start(10)
            self.pbStartStopMonitor.setText('Stop Monitor')
            self.statusBar().showMessage("Monitor started", 2000)
            #print(self.curves)
            for curve in self.curves:
                curve.setData()
        else:
            self.monitorTimer.stop()
            self.statusBar().showMessage("Monitor stopped", 2000)
            self.pbStartStopMonitor.setText('Start Monitor')

    def closeEvent(self, event):
        self.writeSettings()
        event.accept()

    def createActions(self):
        self.exitAct = QAction("E&xit", self, shortcut="Ctrl+Q",
                statusTip="Exit the application", triggered=self.close)

    def readSettings(self):
        self.settings = QSettings("IBB", "iHSV57 Servo Tool")
        self.move(self.settings.value("pos", QPoint(100, 100)))
        self.resize(self.settings.value("size", QSize(800, 600)))
        self.cbSelectComport.setCurrentText(self.settings.value("comport", self.cbSelectComport.currentText()))

    def writeSettings(self):
        self.settings.setValue("pos", self.pos())
        self.settings.setValue("size", self.size())
        self.settings.setValue("comport", self.cbSelectComport.currentText())
        for curve in self.curves:
            curve.writeSettings()


if __name__ == '__main__':
    import sys

    app = QApplication(sys.argv)
    mainWin = MainWindow()
    mainWin.show()
    sys.exit(app.exec_())
