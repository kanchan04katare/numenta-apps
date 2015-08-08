# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2015, Numenta, Inc.  Unless you have purchased from
# Numenta, Inc. a separate commercial license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

"""
Implements Unicorn's model interface.
"""


from datetime import datetime
import json
import logging
from optparse import OptionParser
import sys

import numpy

from nupic.data import fieldmeta
from nupic.frameworks.opf.modelfactory import ModelFactory


# TODO: can we reuse htmengine primitives here?
from htmengine.algorithms.modelSelection.clusterParams import (
  getScalarMetricWithTimeOfDayParams)
from htmengine.runtime.scalar_metric_utils import (
  MODEL_CREATION_RECORD_THRESHOLD)


g_log = logging.getLogger(__name__)



class _Options(object):

  def __init__(self, modelId):
    """
    :param str modelId: model identifier
    """
    self.modelId = modelId



def _parseArgs():
  """ Parse command-line args

  :rtype: _Options object
  """
  helpString = (
    "%prog [options]\n\n"
    "Start Unicorn Engine that runs a single model.")

  parser = OptionParser(helpString)

  parser.add_option(
    "--model",
    action="store",
    type="string",
    dest="modelId",
    help="Model id string")


  options, positionalArgs = parser.parse_args()

  if len(positionalArgs) != 0:
    msg = ("Command accepts no positional args")
    g_log.error(msg)
    parser.error(msg)

  if not options.modelId:
    msg = ("Missing or empty model id")
    g_log.error(msg)
    parser.error(msg)


  return _Options(modelId=options.modelId)



class _Anomalizer(object):
  """ This class is responsible for anomaly likelihood processing

  NOTE: consider modifying htmengine's anomaly_likelihood_helper.py so that we
  can share it with htmengine's Anomaly Service


  TODO Flesh me out
  """

  def process(self, timestamp, metricValue, rawAnomalyScore):
    """ Perform anomaly likelihood processing

    :param datetime.datetime timestamp: metric data sample's timestamp
    :param number metricValue: scalar value of metric data sample; float or int
    :param float rawAnomalyScore: raw anomaly score computed by NuPIC model

    :returns: anomaly likelihood value
    :rtype: float
    """
    pass



class _ModelRunner(object):
  """ Use OPF Model to process metric data samples from stdin and and emit
  anomaly likelihood results to stdout
  """

  # Input column meta info compatible with parameters generated by
  # getScalarMetricWithTimeOfDayParams
  # of htmengine.algorithms.selection.clusterParams
  _INPUT_RECORD_SCHEMA = (
    fieldmeta.FieldMetaInfo("c0", fieldmeta.FieldMetaType.datetime,
                            fieldmeta.FieldMetaSpecial.timestamp),
    fieldmeta.FieldMetaInfo("c1", fieldmeta.FieldMetaType.float,
                            fieldmeta.FieldMetaSpecial.none),
  )


  # minimum resolution of metric; used to set up encoders; if None,
  # getScalarMetricWithTimeOfDayParams will apply its default.
  #
  # TODO: is default acceptable?
  _MIN_METRIC_RESOLUTION = None


  def __init__(self, modelId):
    self._modelId = modelId
    self._model = None
    self._anomalizer = _Anomalizer()


  @classmethod
  def _getInputMessage(cls):
    """Wait for and return next input message from stdin

    NOTE: broken out to be helpful with unit tests

    :returns: newline-terminated json-encoded input message
    :rtype: str
    """
    return sys.stdin.readline()


  @classmethod
  def _emitOutputMessage(cls, message):
    """Emit output message to stdout

    NOTE: broken out to be helpful with unit tests

    :param str message: output message
    """
    sys.stdout.write(message)
    sys.stdout.flush()


  @classmethod
  def _createModel(cls, samples):
    """Instantiate and configure an OPF model

    :param list samples: metric data samples used to calculate minVal and
      maxVal; each element is a two-tuple of
      (<datetime-timestamp>, <float-scalar>)
    :returns: OPF Model instance
    """
    # Generate swarm params
    possibleModels = getScalarMetricWithTimeOfDayParams(
      metricData=numpy.array(zip(*samples)[1]),
      minResolution=cls._MIN_METRIC_RESOLUTION)

    swarmParams = possibleModels[0]

    model = ModelFactory.create(modelConfig=swarmParams["modelConfig"])
    model.enableLearning()
    model.enableInference(swarmParams["inferenceArgs"])


  def _processInputRow(self, inputRow, rowIndex):
    """ Compute anomaly likelihood and emit it via stdout

    :param tuple inputRow: two-tuple input metric data row
      (<datetime-timestamp>, <float-scalar>)
    :param int rowIndex: 0-based index of input row
    """
    # Generate raw anomaly score
    # TODO: opfModelInputRecordFromSequence doesn't exist yet
    inputRecord = opfModelInputRecordFromSequence(inputRow,
                                                  self._INPUT_RECORD_SCHEMA)
    rawAnomalyScore = self._model.run(inputRecord).inferences["anomalyScore"]

    # Generate anomaly likelihood
    anomalyLikelihood = self._anomalizer.process(
      timestamp=inputRow[0],
      metricValue=inputRow[1],
      rawAnomalyScore=rawAnomalyScore)

    # Build output message
    outputMessage = "%s\n" % (
      json.dumps([rowIndex, anomalyLikelihood]),)

    # Emit result
    self._emitOutputMessage(outputMessage)


  def run(self):
    g_log.info("Processing model=%s", self._modelId)

    metricBuffer = []

    rxRowCount = 0
    while True:
      # Read and decode the next sample
      inputMessage = self._getInputMessage()
      timestamp, scalarValue = json.loads(inputMessage)
      timestamp = datetime.utcfromtimestamp(timestamp)
      inputFields = (timestamp, scalarValue)

      rxRowCount += 1

      if self._model is None:
        # Didn't have enough input samples for stats yet
        metricBuffer.append(inputFields)

        if rxRowCount == MODEL_CREATION_RECORD_THRESHOLD:
          # We now have enough data samples to get stats and create the model
          self._model = self._createModel(metricBuffer)

          # Process accumulated input metric data samples
          for inputFields, rowIndex in zip(metricBuffer, xrange(rxRowCount)):
            self._processInputRow(inputRow=inputFields, rowIndex=rowIndex)


        continue

      else:
        # Already have a model
        self._processInputRow(inputRow=inputFields, rowIndex=rxRowCount-1)



def main():
  try:

    options = _parseArgs()

    # Process
    _ModelRunner(options.modelId).run()

  except Exception as ex:  # pylint: disable=W0703
    g_log.exception("Engine failed")

    try:
      # Preserve the original exception context
      raise
    finally:

      errorMessage = {
        "errorText": str(ex) or repr(ex),
        "diagnosticInfo": repr(ex)
      }

      errorMessage = "%s\n" % (json.dumps(errorMessage))

      try:
        sys.stderr.write(errorMessage)
      except Exception:  # pylint: disable=W0703
        g_log.exception("Failed to emit error message to stderr; msg=%s",
                        errorMessage)



if __name__ == "__main__":
  main()
