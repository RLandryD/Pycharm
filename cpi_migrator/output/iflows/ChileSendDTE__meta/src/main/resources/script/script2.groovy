import com.sap.gateway.ip.core.customdev.util.Message
import groovy.json.JsonSlurper
import java.util.Base64.Encoder

def Message processData(Message message) {
    def body = message.getBody(String.class)
    message.setProperty('receiveEvents',body)
    Reader json = message.getBody(java.io.Reader)
    def input = new JsonSlurper().parse(json)

    // Handle Receive Events
    if (input.ReceiveEvents) {
        def catenax_Batch = getKeyAssignments(input.ReceiveEvents.KeyAssignments, 'CATENA_X_BATCH')
        def catenax_Vendor
        if (input.ReceiveEvents.DeliveryItemKeys instanceof List) {
            catenax_Vendor = getKeyAssignments(input.ReceiveEvents.DeliveryItemKeys[0].KeyAssignments, 'CATENA_X_VENDOR')
        } else if(input.ReceiveEvents.DeliveryItemKeys.KeyAssignments) {
            catenax_Vendor = getKeyAssignments(input.ReceiveEvents.DeliveryItemKeys.KeyAssignments, 'CATENA_X_VENDOR')
        }
        if (!catenax_Batch && catenax_Vendor) {
            message.setHeader('queryAAS', 'Y')
//            def assetIds = new StringBuilder()
//            assetIds.append('assetIds=')
            def urlExtend = new StringBuilder()
            urlExtend.append('assetIds=')
            if (input.ReceiveEvents.BatchID) {
                urlExtend.append('[{\"name\":\"batchId\",\"value\":\"').append(input.ReceiveEvents.BatchID).append('\"}')
                if (input.ReceiveEvents.ProductID) {
                    urlExtend.append(',{\"name\":\"manufacturerPartId\",\"value\":\"').append(input.ReceiveEvents.ProductID).append('\"}')
                }
                urlExtend.append(',{\"name\":\"manufacturerId\",\"value\":\"').append(catenax_Vendor).append('\"}]')
//                Encoder encoder = Base64.getUrlEncoder()
//                encodedUrlExtend = encoder.encodeToString(urlExtend.toString().bytes)
//                assetIds.append(encodedUrlExtend)
//                message.setHeader('urlFilter', assetIds.toString())
                message.setHeader('urlFilter', urlExtend.toString())
            }
        } else {
            message.setHeader('queryAAS', 'N')
        }
    } else if (input.ReceiveSerialNumberEvents) {
        def catenax_Serial = getKeyAssignments(input.ReceiveSerialNumberEvents.SerialNumbers.KeyAssignments, 'CATENA_X_VENDOR_PART')
        def catenax_Vendor = getKeyAssignments(input.ReceiveSerialNumberEvents.KeyAssignments,'CATENA_X_VENDOR')
        if(!catenax_Serial && catenax_Vendor) {
            message.setHeader('queryAAS', 'Y')
//            def assetIds = new StringBuilder()
//            assetIds.append('assetIds=')
            def urlExtend = new StringBuilder()
            urlExtend.append('assetIds=')
            if (input.ReceiveSerialNumberEvents.SerialNumbers.SerialID) {
                urlExtend.append('[{\"name\":\"partInstanceId\",\"value\":\"').append(input.ReceiveSerialNumberEvents.SerialNumbers.SerialID).append('\"}')
                if (input.ReceiveSerialNumberEvents.ProductID) {
                    urlExtend.append(',{\"name\":\"manufacturerPartId\",\"value\":\"').append(input.ReceiveSerialNumberEvents.ProductID).append('\"}')
                }
                urlExtend.append(',{\"name\":\"manufacturerId\",\"value\":\"').append(catenax_Vendor).append('\"}]')
//                Encoder encoder = Base64.getUrlEncoder()
//                encodedUrlExtend = encoder.encodeToString(urlExtend.toString().bytes)
//                assetIds.append(encodedUrlExtend)
//                message.setHeader('urlFilter', assetIds.toString())
                message.setHeader('urlFilter', urlExtend.toString())
            }
        } else {
            message.setHeader('queryAAS', 'N')
        }
    }
    return message
}

def getKeyAssignments(def keyAssignments, def qualifier) {
    def  res = ""
    if (keyAssignments) {
        if (keyAssignments instanceof List) {
            keyAssignments.each{
                if (it.Qualifier == qualifier) {
                    res = it.Value
                }
            }
	    }else if (keyAssignments.Qualifier == qualifier) {
            res = keyAssignments.Value
        } else {
            res = ''
        }
    } else {
        res = ''
    }
    return res
}
