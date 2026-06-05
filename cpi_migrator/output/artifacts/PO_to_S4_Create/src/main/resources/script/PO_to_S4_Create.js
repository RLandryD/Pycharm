//To copy existing characterstic object(other than value remaining fields will be copied)
function shallowCopy(original) {
    var copy = {}; // Create an empty object to store the copy
    for (var key in original) {
        copy[key] = original[key]; // Copy each key-value pair from the original to the copy
    }
    return copy;
}

//check if subitem is an array will send individual subitems to transformSubItemFunction
function convertsubItems(subItemList, parentId, convertedsubItems) {
    if (Array.isArray(subItemList)) {
        subItemList.forEach(function (subitem) {
            transformSubItem(subitem, parentId, convertedsubItems);
        });
    } else {
        if (subItemList === "") {
            return;
        }
        transformSubItem(subItemList, parentId, convertedsubItems);
    }
}

//single subitem will be converted into flat with parentId
function transformSubItem(subItem, parentId, convertedsubItems) {
    if (!subItem) {
        return;
    }
    subItem.parentId = parentId; // Set the parentId property of the subitem
    const nestedSubItems = subItem.subItems;
    subItem.subItems = null;
    if (nestedSubItems && subItem.hasOwnProperty('subItems')) {
        delete subItem.subItems;
    }
    subItem.characteristics = transformCharacterstic(subItem.characteristics);
    convertedsubItems.push(subItem); // Push the modified subitem to the 'convertedsubItems' array
    if (nestedSubItems) {
        convertsubItems(nestedSubItems, subItem.id, convertedsubItems);
    }
}

//To change Characterstics with multiple values to multiple characterstic with a single value
function transformValuesFromCharacterstic(characteristics) {
    if (!('values' in characteristics)) {
        return characteristics;
    }
    if (!Array.isArray(characteristics.values) || (characteristics.values.length < 1)) {
        return characteristics;
    }
    var transformedCharacteristics = [];
    var values = characteristics.values;
    values.forEach(function (value) {
        var transChar = shallowCopy(characteristics);
        transChar.values = [value];
        transformedCharacteristics.push(transChar);
    });
    return transformedCharacteristics;
}

//if characterstics is array then each value will be sent to transformedcharacterstics function
function transformCharacterstic(characteristics) {
    var result = [];
    if (characteristics === null) {
        return characteristics;
    }
    if (Array.isArray(characteristics)) {
        if (characteristics.length == 0) {
            return characteristics;
        }
        result = characteristics.map(function (characteristic) {
            return transformValuesFromCharacterstic(characteristic);

        });
    } else {
        return transformValuesFromCharacterstic(characteristics);
    }

    return result;
}

function processData(message) {

    var body = String(message.getBody(new java.lang.String().getClass()));
    var jsonParse = JSON.parse(body);

    const convertedItems = [];

    var rootsubItems = jsonParse.ExternalConfiguration.rootItem.subItems;
    rootItem = jsonParse.ExternalConfiguration.rootItem;
    rootItem.characteristics = transformCharacterstic(rootItem.characteristics);
    convertsubItems(rootsubItems, null, convertedItems);
    if (convertedItems.length) {
        jsonParse.ExternalConfiguration.subItems = convertedItems;
    }

    jsonParse.ExternalConfiguration.rootItem.subItems = null;
    if (jsonParse.ExternalConfiguration.rootItem.hasOwnProperty('subItems') && jsonParse.ExternalConfiguration.rootItem.subItems === null) {
        delete jsonParse.ExternalConfiguration.rootItem.subItems;
    }

    message.setBody(JSON.stringify(jsonParse.ExternalConfiguration, null, 2));

    return message;
}