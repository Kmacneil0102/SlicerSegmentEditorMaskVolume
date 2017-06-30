import os
import vtk, qt, ctk, slicer
import logging
from SegmentEditorEffects import *

class SegmentEditorEffect(AbstractScriptedSegmentEditorEffect):
  """This effect uses a currently existing segment to mask the master volume with a chosen voxel fill value."""

  def __init__(self, scriptedEffect):
    scriptedEffect.name = 'Mask volume'
    scriptedEffect.perSegment = True # this effect operates on a single selected segment
    AbstractScriptedSegmentEditorEffect.__init__(self, scriptedEffect)

  def clone(self):
    # It should not be necessary to modify this method
    import qSlicerSegmentationsEditorEffectsPythonQt as effects
    clonedEffect = effects.qSlicerSegmentEditorScriptedEffect(None)
    clonedEffect.setPythonSource(__file__.replace('\\','/'))
    return clonedEffect

  def icon(self):
    # It should not be necessary to modify this method
    iconPath = os.path.join(os.path.dirname(__file__), 'SegmentEditorEffect.png')
    if os.path.exists(iconPath):
      return qt.QIcon(iconPath)
    return qt.QIcon()

  def helpText(self):
    return """<html>Use currently selected segment as a mask.<br> The mask is applied to the master volume.
</html>"""

  def setupOptionsFrame(self):
    # mask inside/outside the surface checkbox
    self.maskOutsideSurfaceCheckBox = qt.QCheckBox()
    self.maskOutsideSurfaceCheckBox.checked = False
    self.maskOutsideSurfaceCheckBox.setToolTip("If checked, voxel values will be filled outside the segment.")
    self.scriptedEffect.addLabeledOptionsWidget("Mask outside: ", self.maskOutsideSurfaceCheckBox)

    # outside fill value
    self.fillValueEdit = qt.QSpinBox()
    self.fillValueEdit.setToolTip("Choose the voxel intensity that will be used to fill the masked region.")
    self.fillValueEdit.minimum = -32768
    self.fillValueEdit.maximum = 65535
    self.scriptedEffect.addLabeledOptionsWidget("Fill value: ", self.fillValueEdit)

    # output volume selector
    self.outputVolumeSelector = slicer.qMRMLNodeComboBox()
    self.outputVolumeSelector.nodeTypes = ( ("vtkMRMLScalarVolumeNode"), "" )
    self.outputVolumeSelector.selectNodeUponCreation = True
    self.outputVolumeSelector.addEnabled = True
    self.outputVolumeSelector.removeEnabled = True
    self.outputVolumeSelector.noneEnabled = False
    self.outputVolumeSelector.showHidden = False
    self.outputVolumeSelector.setMRMLScene( slicer.mrmlScene )
    self.outputVolumeSelector.setToolTip( "Masked output volume. It may be the same as the input volume for cumulative masking." )
    self.scriptedEffect.addLabeledOptionsWidget("Output Volume: ", self.outputVolumeSelector)

    # Apply button
    self.applyButton = qt.QPushButton("Apply")
    self.applyButton.objectName = self.__class__.__name__ + 'Apply'
    self.applyButton.setToolTip("Apply segment as volume mask")
    self.scriptedEffect.addOptionsWidget(self.applyButton)
    self.applyButton.connect('clicked()', self.onApply)

  def createCursor(self, widget):
    # Turn off effect-specific cursor for this effect
    return slicer.util.mainWindow().cursor

  def setMRMLDefaults(self):
    self.scriptedEffect.setParameterDefault("MaskOutsideSurface", "1")
    self.scriptedEffect.setParameterDefault("FillValue", "0")

  def updateGUIFromMRML(self):
    self.maskOutsideSurfaceCheckBox.setChecked(self.scriptedEffect.parameter("MaskOutsideSurface"))
    self.fillValueEdit.setValue(float(self.scriptedEffect.parameter("FillValue")))

  def onApply(self):
    # Allow users revert to this state by clicking Undo
    self.scriptedEffect.saveStateForUndo()

    inputVolume = self.scriptedEffect.parameterSetNode().GetMasterVolumeNode()
    outputVolume = self.outputVolumeSelector.currentNode()
    maskOutsideSurface = self.maskOutsideSurfaceCheckBox.checked
    fillValue = self.fillValueEdit.value

    segmentID = self.scriptedEffect.parameterSetNode().GetSelectedSegmentID()
    segmentationNode = self.scriptedEffect.parameterSetNode().GetSegmentationNode()
    maskingModel = slicer.vtkMRMLModelNode()
    outputPolyData = vtk.vtkPolyData()
    slicer.vtkSlicerSegmentationsModuleLogic.GetSegmentClosedSurfaceRepresentation(segmentationNode, segmentID, outputPolyData)
    maskingModel.SetAndObservePolyData(outputPolyData)

    self.maskVolumeWithSegment(inputVolume, maskingModel, maskOutsideSurface, fillValue, outputVolume)
    qt.QApplication.restoreOverrideCursor()

    #De-select effect
    self.scriptedEffect.selectEffect("")


  def maskVolumeWithSegment(self, inputVolume, maskingModel, maskOutsideSurface, fillValue, outputVolume):
    """
    Fill voxels of the input volume inside/outside the masking model with the provided fill value
    """

    # Determine the transform between the box and the image IJK coordinate systems

    rasToModel = vtk.vtkMatrix4x4()
    if maskingModel.GetTransformNodeID() != None:
      modelTransformNode = slicer.mrmlScene.GetNodeByID(maskingModel.GetTransformNodeID())
      boxToRas = vtk.vtkMatrix4x4()
      modelTransformNode.GetMatrixTransformToWorld(boxToRas)
      rasToModel.DeepCopy(boxToRas)
      rasToModel.Invert()

    ijkToRas = vtk.vtkMatrix4x4()
    inputVolume.GetIJKToRASMatrix(ijkToRas)

    ijkToModel = vtk.vtkMatrix4x4()
    vtk.vtkMatrix4x4.Multiply4x4(rasToModel, ijkToRas, ijkToModel)
    modelToIjkTransform = vtk.vtkTransform()
    modelToIjkTransform.SetMatrix(ijkToModel)
    modelToIjkTransform.Inverse()

    transformModelToIjk = vtk.vtkTransformPolyDataFilter()
    transformModelToIjk.SetTransform(modelToIjkTransform)
    transformModelToIjk.SetInputConnection(maskingModel.GetPolyDataConnection())

    # Use the stencil to fill the volume

    # Convert model to stencil
    polyToStencil = vtk.vtkPolyDataToImageStencil()
    polyToStencil.SetInputConnection(transformModelToIjk.GetOutputPort())
    polyToStencil.SetOutputSpacing(inputVolume.GetImageData().GetSpacing())
    polyToStencil.SetOutputOrigin(inputVolume.GetImageData().GetOrigin())
    polyToStencil.SetOutputWholeExtent(inputVolume.GetImageData().GetExtent())

    # Apply the stencil to the volume
    stencilToImage = vtk.vtkImageStencil()
    stencilToImage.SetInputConnection(inputVolume.GetImageDataConnection())
    stencilToImage.SetStencilConnection(polyToStencil.GetOutputPort())
    if maskOutsideSurface:
      stencilToImage.ReverseStencilOff()
    else:
      stencilToImage.ReverseStencilOn()
    stencilToImage.SetBackgroundValue(fillValue)
    stencilToImage.Update()

    # Update the volume with the stencil operation result
    outputImageData = vtk.vtkImageData()
    outputImageData.DeepCopy(stencilToImage.GetOutput())

    outputVolume.SetAndObserveImageData(outputImageData);
    outputVolume.SetIJKToRASMatrix(ijkToRas)

    # Add a default display node to output volume node if it does not exist yet
    if not outputVolume.GetDisplayNode:
      displayNode = slicer.vtkMRMLScalarVolumeDisplayNode()
      displayNode.SetAndObserveColorNodeID("vtkMRMLColorTableNodeGrey")
      slicer.mrmlScene.AddNode(displayNode)
      outputVolume.SetAndObserveDisplayNodeID(displayNode.GetID())

    return True